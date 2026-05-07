#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════╗
║           PromptCraft — Prompt Engineering CLI        ║
║     Local LLM-powered prompt generator & consultant   ║
╚═══════════════════════════════════════════════════════╝

Single-file CLI tool. Dependencies: rich (pip install rich)
Backends: Ollama (localhost:11434) | LM Studio (localhost:1234)

Usage:
    python promptcraft.py
    python promptcraft.py --help
    python promptcraft.py --headless --domain image --technique negative --input "sunset over Cairo"
    python promptcraft.py --quiet --domain code --technique chain_of_thought --input "REST API in Python"
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

# ── Attempt rich import, graceful fallback ─────────────────────────────────────
try:
    from rich import print as rprint
    from rich.align import Align
    from rich.box import ROUNDED
    from rich.columns import Columns
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
    RICH_AVAILABLE = True
except ImportError:
    print("Warning: 'rich' not found. Install it: pip install rich")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS & CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

VERSION = "0.2.0"
PROMPTS_DIR = Path("./prompts")
EXAMPLES_FILE = Path(__file__).parent / "examples.json"
CONFIG_FILE = Path(__file__).parent / "promptcraft_config.json"
PLUGINS_DIR = Path(__file__).parent / "plugins"
CUSTOM_DOMAINS_FILE = Path(__file__).parent / "custom_domains.json"
_CONFIG_MAX_SIZE = 65536  # Max config file size in bytes (64 KB) — prevent memory bomb
_MAX_HISTORY_LENGTH = 200  # Max messages in conversation history
_MAX_MESSAGE_LENGTH = 32000  # Max characters per message sent to LLM
_MAX_SAVE_FILE_SIZE = 1048576  # Max save file size (1 MB)
_STREAM_CHUNK_SIZE = 4096  # Buffer size for streaming reads

BACKENDS = {
    "ollama": {
        "name": "Ollama",
        "base_url": "http://localhost:11434",
        "chat_endpoint": "/api/chat",
        "models_endpoint": "/api/tags",
        "format": "ollama",
    },
    "lmstudio": {
        "name": "LM Studio",
        "base_url": "http://localhost:1234",
        "chat_endpoint": "/v1/chat/completions",
        "models_endpoint": "/v1/models",
        "format": "openai",
    },
}

DOMAINS = {
    "code":    {"icon": "💻", "label": "Code / Programming",    "color": "cyan"},
    "image":   {"icon": "🎨", "label": "Image Generation",       "color": "magenta"},
    "video":   {"icon": "🎬", "label": "Video Generation",       "color": "red"},
    "slides":  {"icon": "📊", "label": "Presentation / Slides",  "color": "yellow"},
    "writing": {"icon": "✍️", "label": "Writing / Copywriting",  "color": "green"},
    "music":   {"icon": "🎵", "label": "Music / Audio",          "color": "blue"},
    "agent":   {"icon": "🤖", "label": "AI Agent / System",      "color": "bright_cyan"},
    "custom":  {"icon": "⚡", "label": "Custom / Other",         "color": "white"},
}

TECHNIQUES = {
    "zero_shot": {
        "label": "Zero-Shot",
        "icon": "🎯",
        "desc": "Direct instruction with no examples. Clean and concise.",
        "best_for": ["code", "writing", "agent", "custom"],
    },
    "one_shot": {
        "label": "One-Shot",
        "icon": "1️⃣ ",
        "desc": "One example pair before the main prompt for context.",
        "best_for": ["code", "writing", "slides"],
    },
    "few_shot": {
        "label": "Few-Shot",
        "icon": "🔢",
        "desc": "2-3 example pairs to strongly guide output style.",
        "best_for": ["code", "writing", "image", "music"],
    },
    "instructional": {
        "label": "Instructional",
        "icon": "📋",
        "desc": "Step-by-step numbered directives for structured output.",
        "best_for": ["slides", "writing", "agent", "code"],
    },
    "persona": {
        "label": "Persona-Based",
        "icon": "🎭",
        "desc": "Assigns a specific expert identity to the model.",
        "best_for": ["writing", "code", "agent", "custom"],
    },
    "json_output": {
        "label": "JSON Output",
        "icon": "📦",
        "desc": "Forces structured JSON schema output.",
        "best_for": ["code", "agent", "custom"],
    },
    "chain_of_thought": {
        "label": "Chain-of-Thought",
        "icon": "🔗",
        "desc": "Instructs the model to reason step-by-step before answering.",
        "best_for": ["code", "agent", "custom", "writing"],
    },
    "negative": {
        "label": "Negative Prompting",
        "icon": "🚫",
        "desc": "Adds explicit exclusion list alongside the main prompt.",
        "best_for": ["image", "video", "music", "writing"],
    },
}

# Domain -> recommended techniques (ordered by fit)
DOMAIN_TECHNIQUE_RECOMMENDATIONS = {
    "code":    ["zero_shot", "few_shot", "chain_of_thought", "json_output"],
    "image":   ["negative", "few_shot", "persona", "zero_shot"],
    "video":   ["negative", "instructional", "few_shot", "zero_shot"],
    "slides":  ["instructional", "one_shot", "persona", "zero_shot"],
    "writing": ["persona", "few_shot", "instructional", "zero_shot"],
    "music":   ["negative", "few_shot", "instructional", "zero_shot"],
    "agent":   ["persona", "json_output", "instructional", "chain_of_thought"],
    "custom":  ["zero_shot", "chain_of_thought", "persona", "json_output"],
}

# Domain -> fixed discovery questions (asked before the open chat)
# Each question has: prompt text, optional choices, optional default
DOMAIN_QUESTIONS = {
    "code": [
        {
            "key": "language",
            "prompt": "Programming language",
            "choices": ["Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "Go", "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Other"],
            "default": "1",
        },
        {
            "key": "framework",
            "prompt": "Framework or library (if any)",
            "choices": ["None / Vanilla", "React", "Next.js", "Django", "FastAPI", "Flask", "Spring", "Express", "Vue", "Angular", "Other"],
            "default": "1",
        },
        {
            "key": "code_type",
            "prompt": "Type of code",
            "choices": ["Function / Method", "Class / Module", "API endpoint", "CLI tool", "Script / Automation", "Algorithm", "Full application", "Other"],
            "default": "1",
        },
        {
            "key": "experience",
            "prompt": "Target audience experience level",
            "choices": ["Beginner", "Intermediate", "Advanced", "Expert"],
            "default": "2",
        },
    ],
    "image": [
        {
            "key": "aspect_ratio",
            "prompt": "Aspect ratio / orientation",
            "choices": ["1:1 (Square)", "16:9 (Landscape)", "9:16 (Portrait)", "4:3 (Landscape)", "3:4 (Portrait)", "2:3 (Portrait)", "3:2 (Landscape)", "21:9 (Ultrawide)", "Custom"],
            "default": "1",
        },
        {
            "key": "resolution",
            "prompt": "Target resolution",
            "choices": ["512x512", "768x768", "1024x1024", "1024x1792", "1792x1024", "Not sure / Flexible"],
            "default": "3",
        },
        {
            "key": "style",
            "prompt": "Visual style",
            "choices": ["Photorealistic", "Digital art", "Oil painting", "Watercolor", "Anime / Manga", "3D render", "Pixel art", "Sketch / Line art", "Illustration", "Abstract", "Other"],
            "default": "1",
        },
        {
            "key": "usage",
            "prompt": "Where will this image be used",
            "choices": ["Social media post", "Website hero / banner", "Logo / Icon", "Book cover", "Poster / Print", "Presentation", "App UI", "Other"],
            "default": "1",
        },
    ],
    "video": [
        {
            "key": "duration",
            "prompt": "Video duration",
            "choices": ["5-10 seconds (Clip)", "15-30 seconds (Short)", "1-3 minutes (Medium)", "3-10 minutes (Long)", "10+ minutes (Full)", "Not sure yet"],
            "default": "2",
        },
        {
            "key": "aspect_ratio",
            "prompt": "Aspect ratio",
            "choices": ["16:9 (YouTube / Landscape)", "9:16 (Reels / TikTok / Portrait)", "1:1 (Square / Instagram)", "4:3 (Classic)", "Other"],
            "default": "1",
        },
        {
            "key": "resolution",
            "prompt": "Target resolution",
            "choices": ["720p (HD)", "1080p (Full HD)", "4K (Ultra HD)", "Not sure / Flexible"],
            "default": "2",
        },
        {
            "key": "style",
            "prompt": "Video style",
            "choices": ["Cinematic", "Animated / Motion graphics", "Live action", "Screen recording / Tutorial", "Slideshow", "AI-generated", "Documentary", "Other"],
            "default": "1",
        },
    ],
    "slides": [
        {
            "key": "slide_count",
            "prompt": "Approximate number of slides",
            "choices": ["5 or fewer", "5-10", "10-20", "20-30", "30+", "Not sure yet"],
            "default": "2",
        },
        {
            "key": "audience",
            "prompt": "Target audience",
            "choices": ["Executives / C-suite", "Technical team", "General public", "Students / Education", "Investors / Pitch", "Conference / Talk", "Internal team", "Other"],
            "default": "1",
        },
        {
            "key": "format",
            "prompt": "Presentation format",
            "choices": ["16:9 (Standard)", "4:3 (Classic)", "9:16 (Mobile)", "A4 / Print"],
            "default": "1",
        },
        {
            "key": "tone",
            "prompt": "Tone / Style",
            "choices": ["Professional / Corporate", "Creative / Playful", "Minimal / Clean", "Bold / Dramatic", "Academic / Formal", "Casual / Friendly", "Other"],
            "default": "1",
        },
    ],
    "writing": [
        {
            "key": "content_type",
            "prompt": "Type of content",
            "choices": ["Blog post / Article", "Marketing copy", "Email / Newsletter", "Social media post", "Product description", "Landing page", "Essay / Opinion", "Story / Narrative", "Technical documentation", "Other"],
            "default": "1",
        },
        {
            "key": "tone",
            "prompt": "Writing tone",
            "choices": ["Professional", "Conversational / Casual", "Persuasive / Sales", "Informative / Educational", "Humorous / Witty", "Formal / Academic", "Inspirational / Motivational", "Other"],
            "default": "1",
        },
        {
            "key": "length",
            "prompt": "Target length",
            "choices": ["Short (under 200 words)", "Medium (200-500 words)", "Long (500-1500 words)", "In-depth (1500+ words)", "Flexible / Not sure"],
            "default": "3",
        },
        {
            "key": "audience",
            "prompt": "Target audience",
            "choices": ["General public", "Industry professionals", "Developers / Technical", "Business / Executives", "Students / Beginners", "Children / Young audience", "Other"],
            "default": "1",
        },
    ],
    "music": [
        {
            "key": "genre",
            "prompt": "Music genre",
            "choices": ["Pop", "Rock", "Electronic / EDM", "Hip-Hop / Rap", "Jazz", "Classical", "R&B / Soul", "Country", "Ambient / Lo-fi", "Folk / Acoustic", "Other"],
            "default": "1",
        },
        {
            "key": "duration",
            "prompt": "Target duration",
            "choices": ["Under 30 seconds (Jingle / Ringtone)", "1-2 minutes (Short)", "2-4 minutes (Standard)", "4-8 minutes (Extended)", "8+ minutes (Long-form)", "Not sure yet"],
            "default": "3",
        },
        {
            "key": "mood",
            "prompt": "Mood / Emotion",
            "choices": ["Happy / Upbeat", "Sad / Melancholic", "Energetic / Intense", "Calm / Relaxing", "Dark / Mysterious", "Epic / Grand", "Romantic", "Nostalgic", "Other"],
            "default": "1",
        },
        {
            "key": "purpose",
            "prompt": "Purpose / Use case",
            "choices": ["Background / Ambient", "Song with vocals", "Soundtrack / Score", "Podcast intro / outro", "Commercial / Ad", "Game / App", "Other"],
            "default": "1",
        },
    ],
    "agent": [
        {
            "key": "agent_type",
            "prompt": "Type of AI agent",
            "choices": ["Chatbot / Assistant", "Autonomous worker", "Multi-agent system", "RAG / Knowledge-based", "Tool-using agent", "Data analysis agent", "Workflow automation", "Other"],
            "default": "1",
        },
        {
            "key": "tools",
            "prompt": "What tools/APIs should it use",
            "choices": ["Web search", "Code execution", "File I/O", "Database queries", "API calls", "Browser automation", "None / Pure LLM", "Other"],
            "default": "7",
        },
        {
            "key": "autonomy",
            "prompt": "Level of autonomy",
            "choices": ["Fully autonomous", "Semi-autonomous (asks for approval)", "Step-by-step (human in the loop)", "Interactive / Conversational", "Other"],
            "default": "2",
        },
        {
            "key": "output_format",
            "prompt": "Expected output format",
            "choices": ["Natural language", "Structured JSON", "Markdown report", "Code", "Action plan / Steps", "Other"],
            "default": "1",
        },
    ],
    "custom": [
        {
            "key": "category",
            "prompt": "What category best describes your project",
            "choices": ["Data / Analytics", "Design / Creative", "Education / Learning", "Research / Science", "Business / Strategy", "Personal / Productivity", "Game / Entertainment", "Other"],
            "default": "8",
        },
        {
            "key": "output_type",
            "prompt": "What type of output do you need",
            "choices": ["Text / Prose", "Structured data (JSON, YAML, etc.)", "Step-by-step instructions", "Analysis / Report", "Creative content", "Code / Script", "Other"],
            "default": "1",
        },
        {
            "key": "audience",
            "prompt": "Who is the end user or audience",
            "choices": ["Myself / Personal use", "Team / Internal", "Customers / Public", "Technical users", "Non-technical users", "Mixed / Broad audience", "Other"],
            "default": "1",
        },
        {
            "key": "priority",
            "prompt": "What matters most for this prompt",
            "choices": ["Accuracy / Correctness", "Creativity / Originality", "Speed / Efficiency", "Detail / Thoroughness", "Simplicity / Clarity", "Safety / Compliance", "Other"],
            "default": "1",
        },
    ],
}

CUSTOM_THEME = Theme({
    "info":     "bold cyan",
    "warning":  "bold yellow",
    "error":    "bold red",
    "success":  "bold green",
    "muted":    "dim white",
    "user":     "bold bright_white",
    "bot":      "bold cyan",
    "cmd":      "bold yellow",
    "prompt":   "bold magenta",
    "domain":   "bold bright_cyan",
    "tech":     "bold bright_magenta",
    "plugin":   "bold bright_green",
    "stream":   "cyan",
})

console = Console(theme=CUSTOM_THEME)

# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE 1 — CUSTOM DOMAIN WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def _load_custom_domains() -> dict:
    """Load custom domains from the JSON file. Returns empty dict on failure."""
    if not CUSTOM_DOMAINS_FILE.exists():
        return {}
    try:
        with open(CUSTOM_DOMAINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_custom_domains(domains: dict) -> None:
    """Persist custom domains to disk."""
    try:
        tmp_path = CUSTOM_DOMAINS_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(domains, f, indent=2, ensure_ascii=False)
        tmp_path.replace(CUSTOM_DOMAINS_FILE)
    except OSError as e:
        console.print(f"[warning]Could not save custom domains: {e}[/]")


def _merge_custom_domains() -> None:
    """Merge custom domains from file into the global DOMAINS, QUESTIONS, and RECOMMENDATIONS dicts."""
    custom = _load_custom_domains()
    for key, spec in custom.items():
        if key in DOMAINS:
            continue  # don't overwrite built-in domains
        # Register domain
        DOMAINS[key] = {
            "icon":  spec.get("icon", "⚡"),
            "label": spec.get("label", key.replace("_", " ").title()),
            "color": spec.get("color", "white"),
        }
        # Register questions
        if "questions" in spec:
            DOMAIN_QUESTIONS[key] = spec["questions"]
        # Register technique recommendations
        if "recommended_techniques" in spec:
            DOMAIN_TECHNIQUE_RECOMMENDATIONS[key] = spec["recommended_techniques"]
        # Register which techniques this domain works best for (in TECHNIQUES best_for lists)
        for tech_key in spec.get("recommended_techniques", []):
            if tech_key in TECHNIQUES and key not in TECHNIQUES[tech_key]["best_for"]:
                TECHNIQUES[tech_key]["best_for"].append(key)


def _custom_domain_wizard() -> Optional[str]:
    """Interactive wizard to create a new custom domain. Returns the domain key."""
    console.print()
    console.print(Rule("[domain]Custom Domain Wizard[/]", style="bright_cyan"))
    console.print()
    console.print(Panel(
        "[white]Create your own domain with custom questions and technique preferences.[/]\n"
        "[muted]This domain will be saved and available in future sessions.[/]",
        title="⚡ New Custom Domain",
        border_style="bright_cyan",
        padding=(1, 2),
    ))
    console.print()

    # Step 1: Domain key
    while True:
        domain_key = Prompt.ask("[info]Domain key[/]", default="my_domain").strip().lower()
        domain_key = domain_key.replace(" ", "_").replace("-", "_")
        domain_key = _sanitize_string(domain_key, max_len=32)
        # Only allow alphanumeric + underscore
        domain_key = "".join(c for c in domain_key if c.isalnum() or c == "_")
        if not domain_key:
            console.print("[error]Domain key cannot be empty.[/]")
            continue
        if domain_key in DOMAINS:
            console.print(f"[warning]Domain key '{domain_key}' already exists. Choose another.[/]")
            continue
        break

    # Step 2: Label
    label = Prompt.ask("[info]Domain label (display name)[/]", default=domain_key.replace("_", " ").title())
    label = _sanitize_string(label, max_len=64)

    # Step 3: Icon
    icon = Prompt.ask("[info]Icon (emoji)[/]", default="⚡")

    # Step 4: Color
    valid_colors = [
        "cyan", "magenta", "red", "yellow", "green", "blue",
        "bright_cyan", "bright_magenta", "white", "bright_white",
    ]
    console.print(f"  [muted]Available colors: {', '.join(valid_colors)}[/]")
    color = Prompt.ask("[info]Theme color[/]", choices=valid_colors, default="white")

    # Step 5: Recommended techniques
    console.print()
    console.print("[info]Select recommended techniques (comma-separated numbers):[/]")
    tech_keys = list(TECHNIQUES.keys())
    for i, tk in enumerate(tech_keys, 1):
        console.print(f"  [muted]{i}.[/] {TECHNIQUES[tk]['icon']} {TECHNIQUES[tk]['label']}")
    tech_input = Prompt.ask("[info]Techniques[/]", default="1,2,3,4")
    selected_techs = []
    for idx_str in tech_input.split(","):
        try:
            idx = int(idx_str.strip())
            if 1 <= idx <= len(tech_keys):
                selected_techs.append(tech_keys[idx - 1])
        except ValueError:
            # Try matching by name
            name = idx_str.strip().lower().replace("-", "_").replace(" ", "_")
            for tk in tech_keys:
                if name in tk or name in TECHNIQUES[tk]["label"].lower():
                    selected_techs.append(tk)
                    break
    if not selected_techs:
        selected_techs = ["zero_shot", "chain_of_thought", "persona"]

    # Step 6: Discovery questions
    console.print()
    console.print(Rule("[info]Discovery Questions[/]", style="cyan"))
    console.print("[muted]Define questions asked before the discovery chat.[/]")
    console.print("[muted]These help tailor the prompt to your specific domain.[/]")
    console.print()

    questions = []
    add_more = True
    q_num = 1
    while add_more:
        console.print(f"  [bold]Question {q_num}:[/]")
        q_key = Prompt.ask(f"    [info]Key (e.g. 'platform', 'budget')[/]", default=f"q{q_num}")
        q_key = _sanitize_string(q_key, max_len=32).lower().replace(" ", "_")
        q_prompt = Prompt.ask(f"    [info]Prompt text[/]", default=q_key.replace("_", " ").title())

        has_choices = Confirm.ask(f"    [info]Multiple choice?[/]", default=True)
        q_choices = []
        q_default = "1"
        if has_choices:
            console.print("    [muted]Enter choices one per line. Empty line to finish.[/]")
            c_num = 1
            while True:
                choice = Prompt.ask(f"      [info]Choice {c_num}[/]", default="")
                if not choice:
                    break
                q_choices.append(_sanitize_string(choice, max_len=64))
                c_num += 1
                if c_num > 10:
                    break
            if not q_choices:
                q_choices = ["Yes", "No", "Not sure"]
        else:
            q_default = ""

        questions.append({
            "key": q_key,
            "prompt": q_prompt,
            "choices": q_choices,
            "default": q_default,
        })

        q_num += 1
        add_more = Confirm.ask("[info]Add another question?[/]", default=q_num <= 3)

    # Build the domain spec
    domain_spec = {
        "icon": icon,
        "label": label,
        "color": color,
        "recommended_techniques": selected_techs,
        "questions": questions,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Save to file
    custom = _load_custom_domains()
    custom[domain_key] = domain_spec
    _save_custom_domains(custom)

    # Merge into runtime
    _merge_custom_domains()

    # Show summary
    console.print()
    console.print(Panel(
        f"[bold]Key        :[/] {domain_key}\n"
        f"[bold]Label      :[/] {icon} {label}\n"
        f"[bold]Color      :[/] {color}\n"
        f"[bold]Techniques :[/] {', '.join(TECHNIQUES[t]['label'] for t in selected_techs)}\n"
        f"[bold]Questions  :[/] {len(questions)} defined\n"
        f"[bold]Saved to   :[/] {CUSTOM_DOMAINS_FILE}",
        title="[success]Custom Domain Created[/]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()

    return domain_key


def _list_custom_domains() -> None:
    """Display all saved custom domains."""
    custom = _load_custom_domains()
    if not custom:
        console.print("[muted]No custom domains found. Use /wizard to create one.[/]")
        return

    table = Table(show_header=True, header_style="bold bright_cyan", box=ROUNDED, padding=(0, 2))
    table.add_column("Key",         style="muted", no_wrap=True, width=16)
    table.add_column("Label",       style="bold white", width=24)
    table.add_column("Questions",   justify="center", width=10)
    table.add_column("Techniques",  style="white", width=30)
    table.add_column("Created",     style="muted", width=20)

    for key, spec in custom.items():
        techs = spec.get("recommended_techniques", [])
        tech_labels = ", ".join(TECHNIQUES.get(t, {}).get("label", t) for t in techs[:3])
        if len(techs) > 3:
            tech_labels += f" +{len(techs)-3}"
        table.add_row(
            key,
            f"{spec.get('icon', '⚡')} {spec.get('label', key)}",
            str(len(spec.get("questions", []))),
            tech_labels,
            spec.get("created_at", "unknown"),
        )

    console.print(Panel(table, title="[domain]Custom Domains[/]", border_style="bright_cyan"))


def _delete_custom_domain(key: str) -> None:
    """Delete a custom domain by key."""
    custom = _load_custom_domains()
    if key not in custom:
        console.print(f"[warning]Custom domain '{key}' not found.[/]")
        return
    del custom[key]
    _save_custom_domains(custom)
    console.print(f"[success]Deleted custom domain '{key}'.[/]")


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE 2 — PLUGIN SYSTEM FOR EXAMPLES
# ══════════════════════════════════════════════════════════════════════════════

class PluginManager:
    """Discovers and loads example plugins from the plugins/ directory.

    Plugin structure:
        plugins/
          my_plugin/
            plugin.json   — metadata (name, version, description, domains)
            examples.json — few_shot_examples + one_shot_examples
          another_plugin/
            plugin.json
            examples.json
          advanced_plugin/
            plugin.json
            examples.json
            plugin.py     — optional Python hook: on_load(engine) -> dict of examples
    """

    def __init__(self):
        self.plugins: dict[str, dict] = {}  # plugin_name -> metadata
        self.examples: dict = {"few_shot_examples": {}, "one_shot_examples": {}}
        self.loaded = False

    def discover(self) -> list[str]:
        """Scan the plugins directory and return a list of discovered plugin names."""
        if not PLUGINS_DIR.exists():
            return []

        found = []
        for entry in sorted(PLUGINS_DIR.iterdir()):
            if entry.is_dir():
                meta_file = entry / "plugin.json"
                if meta_file.exists():
                    found.append(entry.name)
        return found

    def load_all(self) -> dict:
        """Load all discovered plugins. Returns merged examples dict."""
        if self.loaded:
            return self.examples

        plugin_names = self.discover()
        if not plugin_names:
            self.loaded = True
            return self.examples

        console.print()
        console.print(Rule("[plugin]Plugin System[/]", style="bright_green"))

        for name in plugin_names:
            self._load_plugin(name)

        if self.plugins:
            table = Table(show_header=True, header_style="bold bright_green", box=ROUNDED, padding=(0, 2))
            table.add_column("Plugin",       style="bold white", width=20)
            table.add_column("Version",      style="muted", width=10)
            table.add_column("Domains",      style="white", width=30)
            table.add_column("Examples",     justify="center", width=10)
            table.add_column("Status",       justify="center", width=12)

            for pname, meta in self.plugins.items():
                domains_str = ", ".join(meta.get("domains", [])[:4])
                if len(meta.get("domains", [])) > 4:
                    domains_str += f" +{len(meta['domains'])-4}"
                ex_count = meta.get("_example_count", 0)
                table.add_row(
                    pname,
                    meta.get("version", "?"),
                    domains_str,
                    str(ex_count),
                    "[success]Loaded[/]",
                )

            console.print(Panel(table, title="[plugin]Loaded Plugins[/]", border_style="bright_green"))
        else:
            console.print("[muted]No valid plugins found in plugins/ directory.[/]")

        self.loaded = True
        return self.examples

    def _load_plugin(self, name: str) -> bool:
        """Load a single plugin by directory name."""
        plugin_dir = PLUGINS_DIR / name
        meta_file  = plugin_dir / "plugin.json"
        ex_file    = plugin_dir / "examples.json"
        py_file    = plugin_dir / "plugin.py"

        # Load metadata
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[warning]Plugin '{name}': invalid plugin.json — {e}[/]")
            return False

        # Validate metadata
        if not isinstance(meta, dict) or "name" not in meta:
            console.print(f"[warning]Plugin '{name}': missing 'name' in plugin.json[/]")
            return False

        # Load examples from JSON
        plugin_examples = {"few_shot_examples": {}, "one_shot_examples": {}}
        if ex_file.exists():
            try:
                with open(ex_file, "r", encoding="utf-8") as f:
                    plugin_examples = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"[warning]Plugin '{name}': invalid examples.json — {e}[/]")

        # Load Python hook if present
        if py_file.exists():
            py_examples = self._load_python_hook(py_file, name)
            if py_examples:
                # Python hook results override JSON for same domains
                for category in ("few_shot_examples", "one_shot_examples"):
                    for domain_key, examples in py_examples.get(category, {}).items():
                        plugin_examples.setdefault(category, {})[domain_key] = examples

        # Merge into global examples
        example_count = 0
        for category in ("few_shot_examples", "one_shot_examples"):
            for domain_key, examples in plugin_examples.get(category, {}).items():
                if isinstance(examples, list):
                    self.examples.setdefault(category, {}).setdefault(domain_key, []).extend(examples)
                    example_count += len(examples)
                elif isinstance(examples, dict):  # single example (one_shot)
                    self.examples.setdefault(category, {})[domain_key] = examples
                    example_count += 1

        # Store metadata
        meta["_example_count"] = example_count
        self.plugins[name] = meta
        return True

    def _load_python_hook(self, py_file: Path, name: str) -> Optional[dict]:
        """Load and execute a Python plugin hook. Returns examples dict or None."""
        try:
            spec = importlib.util.spec_from_file_location(f"promptcraft_plugin_{name}", py_file)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "on_load"):
                result = module.on_load()
                if isinstance(result, dict):
                    return result
        except Exception as e:
            console.print(f"[warning]Plugin '{name}': Python hook error — {e}[/]")
        return None

    def get_plugin_info(self, name: str) -> Optional[dict]:
        """Get metadata for a specific plugin."""
        return self.plugins.get(name)

    def list_plugins(self, domain: str = None, technique: str = None) -> None:
        """Display detailed info about loaded plugins.

        Args:
            domain:    If set, only show plugins that cover this domain.
            technique: If set, only show plugins that have examples for this technique
                       (few_shot_examples for few_shot, one_shot_examples for one_shot).
        """
        if not self.plugins:
            plugin_names = self.discover()
            if not plugin_names:
                console.print(Panel(
                    "[muted]No plugins found.[/]\n\n"
                    "[white]Create a plugin by adding a directory to:[/]\n"
                    f"[cmd]{PLUGINS_DIR}/<plugin_name>/plugin.json[/]\n\n"
                    "[white]plugin.json format:[/]\n"
                    '[muted]{\n  "name": "My Plugin",\n  "version": "1.0",\n  "description": "...",\n  "domains": ["image", "code"]\n}[/]\n\n'
                    "[white]Add examples.json with few_shot_examples and one_shot_examples.[/]",
                    title="[plugin]Plugin System[/]",
                    border_style="bright_green",
                    padding=(1, 2),
                ))
                return

        # ── Filter plugins by domain and technique ──────────────────────────
        filtered: dict[str, dict] = {}
        for pname, meta in self.plugins.items():
            # Domain filter: plugin must list the session's domain in its domains
            if domain:
                plugin_domains = [d.lower() for d in meta.get("domains", [])]
                if plugin_domains and domain not in plugin_domains:
                    continue

            # Technique filter: plugin must have examples relevant to the technique
            if technique:
                has_relevant_examples = False
                if technique == "few_shot":
                    has_relevant_examples = bool(
                        self.examples.get("few_shot_examples", {}).get(domain)
                    )
                elif technique == "one_shot":
                    has_relevant_examples = bool(
                        self.examples.get("one_shot_examples", {}).get(domain)
                    )
                else:
                    # For non-example techniques (zero_shot, persona, etc.),
                    # show the plugin if it covers the domain at all
                    has_relevant_examples = True
                if not has_relevant_examples:
                    continue

            filtered[pname] = meta

        # ── Display ─────────────────────────────────────────────────────────
        if not filtered:
            domain_label = DOMAINS.get(domain, {}).get("label", domain) if domain else "N/A"
            tech_label = TECHNIQUES.get(technique, {}).get("label", technique) if technique else "N/A"
            console.print(Panel(
                f"[muted]No plugins match your current session.[/]\n\n"
                f"[bold]Current domain   :[/] {domain_label}\n"
                f"[bold]Current technique:[/] {tech_label}\n\n"
                f"[white]{len(self.plugins)} plugin(s) loaded, but none cover this domain/technique combination.[/]\n\n"
                f"[muted]Use [cmd]/plugins all[/] to see every loaded plugin, or install a plugin\n"
                f"that covers the {domain_label} domain with the {tech_label} technique.[/]",
                title="[plugin]Matching Plugins[/]",
                border_style="bright_green",
                padding=(1, 2),
            ))
            return

        # Header showing what we're filtering by
        domain_label = DOMAINS.get(domain, {}).get("label", domain) if domain else "All"
        domain_icon  = DOMAINS.get(domain, {}).get("icon", "") if domain else ""
        tech_label   = TECHNIQUES.get(technique, {}).get("label", technique) if technique else "Any"
        tech_icon    = TECHNIQUES.get(technique, {}).get("icon", "") if technique else ""

        console.print(Panel(
            f"[bold]Domain   :[/] {domain_icon} {domain_label}\n"
            f"[bold]Technique:[/] {tech_icon} {tech_label}\n"
            f"[bold]Matched  :[/] {len(filtered)} of {len(self.plugins)} plugins",
            title="[plugin]Filtering By[/]",
            border_style="bright_green",
            padding=(0, 2),
        ))

        for pname, meta in filtered.items():
            # Highlight which examples are relevant
            example_details = []
            few = self.examples.get("few_shot_examples", {}).get(domain, [])
            one = self.examples.get("one_shot_examples", {}).get(domain)
            if few:
                example_details.append(f"{len(few)} few-shot")
            if one:
                example_details.append("1 one-shot")
            example_str = ", ".join(example_details) if example_details else str(meta.get("_example_count", 0))

            console.print(Panel(
                f"[bold]Name       :[/] {meta.get('name', pname)}\n"
                f"[bold]Version    :[/] {meta.get('version', '?')}\n"
                f"[bold]Description:[/] {meta.get('description', 'N/A')}\n"
                f"[bold]Domains    :[/] {', '.join(meta.get('domains', []))}\n"
                f"[bold]Examples   :[/] {example_str} for {domain_label}\n"
                f"[bold]Path       :[/] {PLUGINS_DIR / pname}",
                title=f"[plugin]{meta.get('name', pname)}[/]",
                border_style="bright_green",
                padding=(1, 2),
            ))


# Global plugin manager instance
plugin_manager = PluginManager()


# ══════════════════════════════════════════════════════════════════════════════
#  SECURITY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_string(value: str, max_len: int = 256) -> str:
    """Strip control characters and limit length of a string."""
    if not isinstance(value, str):
        return ""
    # Remove null bytes and control characters (keep newlines/tabs)
    cleaned = value.replace("\x00", "").replace("\r", "")
    cleaned = "".join(c for c in cleaned if ord(c) >= 32 or c in "\n\t")
    return cleaned[:max_len]


def _validate_url_is_localhost(url: str) -> bool:
    """Ensure a URL points only to localhost / loopback — prevents SSRF."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except Exception:
        return False


def _safe_resolve_path(path: Path) -> Path:
    """Resolve a path and ensure it doesn't escape the expected directory."""
    resolved = path.resolve()
    # For PROMPTS_DIR, ensure it stays under the CWD
    if path == PROMPTS_DIR:
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError:
            raise ValueError(f"Save path escapes working directory: {resolved}")
    return resolved


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard. Returns True on success.

    Tries multiple methods in order:
    1. pyperclip (cross-platform, pip install pyperclip)
    2. xclip (Linux)
    3. xsel (Linux)
    4. wl-copy (Wayland)
    5. pbcopy (macOS)
    6. clip.exe (Windows)
    """
    # Method 1: pyperclip (most reliable cross-platform)
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # Method 2-6: platform-specific CLI tools
    clipboard_commands = [
        (["xclip", "-selection", "clipboard"], "xclip"),
        (["xsel", "--clipboard", "--input"], "xsel"),
        (["wl-copy"], "wl-copy"),
        (["pbcopy"], "pbcopy"),
    ]

    for cmd, name in clipboard_commands:
        try:
            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            continue
        except Exception:
            continue

    # Windows: clip.exe
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["clip.exe"],
                input=text.encode("utf-16"),
                capture_output=True,
                timeout=5,
            )
            return True
        except Exception:
            pass

    return False

# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 1 — BACKEND CLIENT (with streaming support)
# ══════════════════════════════════════════════════════════════════════════════

class BackendClient:
    """Unified client for Ollama and LM Studio via stdlib urllib."""

    def __init__(self, backend_key: str, model: str):
        self.backend_key  = backend_key
        self.backend      = BACKENDS[backend_key]
        self.model        = model

    # ── HTTP helper ────────────────────────────────────────────────────────────
    def _post(self, endpoint: str, payload: dict, timeout: int = 120) -> dict:
        url  = self.backend["base_url"] + endpoint
        # SSRF protection: only allow localhost URLs
        if not _validate_url_is_localhost(url):
            raise ConnectionError(f"Blocked non-localhost URL: {url}")
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Server responded but with an error (400, 404, 500, etc.)
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
                error_json = json.loads(error_body)
                # Try OpenAI-style error nesting first
                error_msg = error_json.get("error", {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", error_body)
                elif isinstance(error_msg, str):
                    pass  # already a string
                else:
                    error_msg = error_body
            except Exception:
                error_msg = error_body or e.reason
            raise ConnectionError(
                f"{self.backend['name']} returned HTTP {e.code}: {error_msg}"
            ) from e
        except urllib.error.URLError as e:
            # Connection-level error (server unreachable, timeout, etc.)
            raise ConnectionError(f"Cannot reach {self.backend['name']}: {e.reason}") from e

    def _get(self, endpoint: str, timeout: int = 10) -> dict:
        url = self.backend["base_url"] + endpoint
        # SSRF protection: only allow localhost URLs
        if not _validate_url_is_localhost(url):
            raise ConnectionError(f"Blocked non-localhost URL: {url}")
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ConnectionError(
                f"{self.backend['name']} returned HTTP {e.code}: {error_body or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach {self.backend['name']}: {e.reason}") from e

    # ── Model listing ──────────────────────────────────────────────────────────
    @staticmethod
    def list_models(backend_key: str) -> list[str]:
        backend = BACKENDS[backend_key]
        url     = backend["base_url"] + backend["models_endpoint"]
        # SSRF protection
        if not _validate_url_is_localhost(url):
            return []
        req     = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        if backend["format"] == "ollama":
            return [m["name"] for m in data.get("models", [])]
        else:  # openai-compat
            return [m["id"] for m in data.get("data", [])]

    # ── Ping ───────────────────────────────────────────────────────────────────
    @staticmethod
    def ping(backend_key: str) -> bool:
        backend = BACKENDS[backend_key]
        url     = backend["base_url"] + backend["models_endpoint"]
        # SSRF protection
        if not _validate_url_is_localhost(url):
            return False
        try:
            with urllib.request.urlopen(url, timeout=4):
                return True
        except Exception:
            return False

    # ── Message sanitisation ────────────────────────────────────────────────────
    @staticmethod
    def sanitize_messages(messages: list[dict]) -> list[dict]:
        """Ensure messages follow strict system -> user/assistant/user/... alternation.

        LM Studio's Jinja template requires roles to strictly alternate
        user/assistant/user/assistant after an optional leading system message.
        Consecutive same-role messages are merged; a missing user turn before
        an assistant turn gets a placeholder user message inserted.
        """
        if not messages:
            return messages

        result: list[dict] = []

        # Allow leading system message(s) — merge them
        system_parts: list[str] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                break

        if system_parts:
            result.append({"role": "system", "content": "\n\n".join(system_parts)})

        # Process remaining non-system messages
        non_system = [m for m in messages if m["role"] != "system"]
        if not non_system:
            # Only system messages — add a user placeholder so the model can respond
            result.append({"role": "user", "content": "Hello"})
            return result

        # If the first non-system message is from assistant, prepend a user turn
        if non_system[0]["role"] == "assistant":
            result.append({"role": "user", "content": "(continued)"})

        # Merge consecutive same-role messages and ensure alternation
        for msg in non_system:
            # Truncate excessively long messages to prevent memory/timeout issues
            content = msg["content"][:_MAX_MESSAGE_LENGTH] if len(msg.get("content", "")) > _MAX_MESSAGE_LENGTH else msg["content"]
            if not result or result[-1]["role"] == "system":
                # First non-system message — just add it
                result.append({"role": msg["role"], "content": content})
            elif result[-1]["role"] == msg["role"]:
                # Consecutive same role — merge content
                result[-1]["content"] += "\n\n" + content
            else:
                # Normal alternation — just add
                result.append({"role": msg["role"], "content": content})

        # Ensure the last message is from the user (model needs to respond)
        if result and result[-1]["role"] == "assistant":
            result.append({"role": "user", "content": "Please continue."})

        return result

    # ── Unified chat (non-streaming) ───────────────────────────────────────────
    def chat(self, messages: list[dict]) -> str:
        """Send messages and return assistant reply as string."""
        if self.backend["format"] == "ollama":
            return self._chat_ollama(messages)
        return self._chat_openai(messages)

    def _chat_ollama(self, messages: list[dict]) -> str:
        safe_messages = self.sanitize_messages(messages)
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "stream": False,
        }
        result = self._post(self.backend["chat_endpoint"], payload)
        return result["message"]["content"].strip()

    def _chat_openai(self, messages: list[dict]) -> str:
        # Sanitise messages to satisfy LM Studio's strict role alternation
        safe_messages = self.sanitize_messages(messages)
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "stream": False,
        }
        try:
            result = self._post(self.backend["chat_endpoint"], payload)
            return result["choices"][0]["message"]["content"].strip()
        except ConnectionError as e:
            error_str = str(e)
            # If model not found, try with the first available model from the server
            if "model" in error_str.lower() and ("not found" in error_str.lower() or "does not exist" in error_str.lower()):
                available = BackendClient.list_models(self.backend_key)
                if available:
                    self.model = available[0]
                    payload["model"] = self.model
                    result = self._post(self.backend["chat_endpoint"], payload)
                    return result["choices"][0]["message"]["content"].strip()
            # If still failing, try without model field (some LM Studio versions ignore it)
            if "model" in error_str.lower():
                payload.pop("model", None)
                try:
                    result = self._post(self.backend["chat_endpoint"], payload)
                    return result["choices"][0]["message"]["content"].strip()
                except ConnectionError:
                    pass  # Fall through to re-raise original
            raise

    # ── FEATURE 3: Streaming chat ──────────────────────────────────────────────
    def chat_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Send messages and yield assistant reply tokens as they arrive.

        Supports both Ollama (NDJSON stream) and OpenAI-compatible (SSE stream)
        streaming formats.
        """
        if self.backend["format"] == "ollama":
            yield from self._chat_stream_ollama(messages)
        else:
            yield from self._chat_stream_openai(messages)

    def _chat_stream_ollama(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream from Ollama's NDJSON streaming endpoint."""
        url = self.backend["base_url"] + self.backend["chat_endpoint"]
        if not _validate_url_is_localhost(url):
            raise ConnectionError(f"Blocked non-localhost URL: {url}")

        safe_messages = self.sanitize_messages(messages)
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "stream": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(_STREAM_CHUNK_SIZE).decode("utf-8", errors="replace")
                    if not chunk:
                        break
                    buffer += chunk
                    # Ollama sends one JSON object per line (NDJSON)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            content = obj.get("message", {}).get("content", "")
                            if content:
                                yield content
                            if obj.get("done", False):
                                return
                        except json.JSONDecodeError:
                            continue
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ConnectionError(
                f"{self.backend['name']} streaming returned HTTP {e.code}: {error_body or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach {self.backend['name']} for streaming: {e.reason}") from e

    def _chat_stream_openai(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream from OpenAI-compatible (LM Studio) SSE endpoint."""
        url = self.backend["base_url"] + self.backend["chat_endpoint"]
        if not _validate_url_is_localhost(url):
            raise ConnectionError(f"Blocked non-localhost URL: {url}")

        safe_messages = self.sanitize_messages(messages)
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "stream": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(_STREAM_CHUNK_SIZE).decode("utf-8", errors="replace")
                    if not chunk:
                        break
                    buffer += chunk
                    # OpenAI SSE format: "data: {json}\n\n"
                    while "\n\n" in buffer:
                        event, buffer = buffer.split("\n\n", 1)
                        for line in event.split("\n"):
                            line = line.strip()
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]  # Remove "data: " prefix
                            if data_str == "[DONE]":
                                return
                            try:
                                obj = json.loads(data_str)
                                delta = obj.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ConnectionError(
                f"{self.backend['name']} streaming returned HTTP {e.code}: {error_body or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach {self.backend['name']} for streaming: {e.reason}") from e


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 2 — PROMPT ENGINE (with plugin support)
# ══════════════════════════════════════════════════════════════════════════════

class PromptEngine:
    """Builds system prompts, applies techniques, synthesizes final prompts."""

    def __init__(self, client: BackendClient, domain: str, technique: str):
        self.client    = client
        self.domain    = domain
        self.technique = technique
        self.examples  = self._load_examples()

    # ── Examples loader (enhanced with plugin support) ─────────────────────────
    def _load_examples(self) -> dict:
        """Load examples from both the built-in file and all plugins."""
        # Start with built-in examples
        examples = {"few_shot_examples": {}, "one_shot_examples": {}}
        if EXAMPLES_FILE.exists():
            try:
                with open(EXAMPLES_FILE, "r", encoding="utf-8") as f:
                    examples = json.load(f)
            except Exception:
                pass

        # Merge plugin examples (plugins extend, don't overwrite)
        plugin_examples = plugin_manager.examples
        for category in ("few_shot_examples", "one_shot_examples"):
            for domain_key, domain_examples in plugin_examples.get(category, {}).items():
                if isinstance(domain_examples, list):
                    examples.setdefault(category, {}).setdefault(domain_key, []).extend(domain_examples)
                elif isinstance(domain_examples, dict):
                    # For one_shot, only set if not already present
                    examples.setdefault(category, {}).setdefault(domain_key, domain_examples)

        return examples

    # ── Discovery system prompt ────────────────────────────────────────────────
    def discovery_system_prompt(self, domain_answers: dict[str, str] = None) -> str:
        domain_label = DOMAINS.get(self.domain, {}).get("label", self.domain.replace("_", " ").title())

        # Build the domain-specific answers block if available
        answers_block = ""
        if domain_answers:
            lines = []
            for key, value in domain_answers.items():
                # Pretty-print the key (e.g. "aspect_ratio" -> "Aspect ratio")
                pretty_key = key.replace("_", " ").title()
                lines.append(f"- {pretty_key}: {value}")
            answers_block = (
                f"\n\nUSER'S DOMAIN-SPECIFIC SPECIFICATIONS (already collected):\n"
                + "\n".join(lines)
                + "\n\nUse these specifications as FIXED constraints. Do NOT ask about them again. "
                "Build on them during discovery."
            )

        return f"""You are PromptCraft, an expert Prompt Engineering consultant specializing in {domain_label}.

Your role in this conversation is to act as a DISCOVERY AGENT:
- Ask targeted, intelligent questions to deeply understand what the user wants
- Uncover details they haven't thought of yet (style, tone, audience, constraints, format, technical specs)
- Keep questions concise — ask 1-2 questions at a time, never overwhelm
- Be conversational and helpful, not robotic
- Remember EVERYTHING said in this chat — it will be used to build the final prompt

Domain focus: {domain_label}
Available prompting technique: {TECHNIQUES[self.technique]['label']}{answers_block}

When the user types /generate, you will stop discovery and a separate synthesis will happen.
Do NOT generate the prompt yourself during chat — only ask questions and acknowledge answers.

Begin by introducing yourself briefly and asking your FIRST smart question about their {domain_label} needs."""

    # ── Technique suggestion ───────────────────────────────────────────────────
    def suggest_technique(self) -> str:
        """Ask LLM which technique fits best, return technique key."""
        domain_label = DOMAINS.get(self.domain, {}).get("label", self.domain.replace("_", " ").title())
        recs = DOMAIN_TECHNIQUE_RECOMMENDATIONS.get(self.domain, list(TECHNIQUES.keys()))
        tech_list = "\n".join(
            f"- {k}: {TECHNIQUES[k]['desc']}" for k in recs
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"I want to generate a prompt for the domain: {domain_label}.\n"
                    f"Based on these available techniques:\n{tech_list}\n\n"
                    f"Which ONE technique is the best fit for {domain_label}? "
                    f"Reply with ONLY the technique key from this list: {', '.join(recs)}. "
                    f"No explanation. Just the key."
                ),
            }
        ]
        try:
            reply = self.client.chat(messages).strip().lower().replace("-", "_").replace(" ", "_")
            # sanitise — must be a known key
            for key in TECHNIQUES:
                if key in reply:
                    return key
        except Exception:
            pass
        return recs[0]  # fallback to top recommendation

    # ── Synthesis ──────────────────────────────────────────────────────────────
    def synthesize(self, history: list[dict], domain_answers: dict[str, str] = None) -> str:
        """Read full conversation history and output the final engineered prompt."""
        domain_label = DOMAINS.get(self.domain, {}).get("label", self.domain.replace("_", " ").title())
        tech         = TECHNIQUES[self.technique]

        # Build technique-specific instructions
        technique_instructions = self._technique_instructions()

        # Build few/one-shot examples block if needed
        examples_block = self._build_examples_block()

        # Build domain specifications block if available
        specs_block = ""
        if domain_answers:
            spec_lines = []
            for key, value in domain_answers.items():
                pretty_key = key.replace("_", " ").title()
                spec_lines.append(f"- {pretty_key}: {value}")
            specs_block = (
                "DOMAIN SPECIFICATIONS (fixed constraints from user):\n"
                + "\n".join(spec_lines) + "\n\n"
                "These are NON-NEGOTIABLE constraints that MUST be reflected in the final prompt.\n\n"
            )

        synthesis_prompt = f"""You are an expert Prompt Engineer. Your task is to synthesize a comprehensive, production-ready prompt.

DOMAIN: {domain_label}
TECHNIQUE: {tech['label']} — {tech['desc']}

{specs_block}CONVERSATION HISTORY (what the user wants):
{self._format_history(history)}

TECHNIQUE APPLICATION RULES:
{technique_instructions}

{examples_block}

OUTPUT INSTRUCTIONS:
- Generate ONLY the final engineered prompt, nothing else
- Do NOT include any preamble, explanation, or "Here is your prompt:"
- The prompt must be comprehensive, using all information from the conversation
- Apply the {tech['label']} technique structure precisely
- Make it immediately usable — copy-paste ready

Generate the prompt now:"""

        messages = [{"role": "user", "content": synthesis_prompt}]
        return self.client.chat(messages)

    # ── Synthesis with streaming ───────────────────────────────────────────────
    def synthesize_stream(self, history: list[dict], domain_answers: dict[str, str] = None) -> Generator[str, None, None]:
        """Stream the synthesized prompt token by token."""
        domain_label = DOMAINS.get(self.domain, {}).get("label", self.domain.replace("_", " ").title())
        tech         = TECHNIQUES[self.technique]

        technique_instructions = self._technique_instructions()
        examples_block = self._build_examples_block()

        specs_block = ""
        if domain_answers:
            spec_lines = []
            for key, value in domain_answers.items():
                pretty_key = key.replace("_", " ").title()
                spec_lines.append(f"- {pretty_key}: {value}")
            specs_block = (
                "DOMAIN SPECIFICATIONS (fixed constraints from user):\n"
                + "\n".join(spec_lines) + "\n\n"
                "These are NON-NEGOTIABLE constraints that MUST be reflected in the final prompt.\n\n"
            )

        synthesis_prompt = f"""You are an expert Prompt Engineer. Your task is to synthesize a comprehensive, production-ready prompt.

DOMAIN: {domain_label}
TECHNIQUE: {tech['label']} — {tech['desc']}

{specs_block}CONVERSATION HISTORY (what the user wants):
{self._format_history(history)}

TECHNIQUE APPLICATION RULES:
{technique_instructions}

{examples_block}

OUTPUT INSTRUCTIONS:
- Generate ONLY the final engineered prompt, nothing else
- Do NOT include any preamble, explanation, or "Here is your prompt:"
- The prompt must be comprehensive, using all information from the conversation
- Apply the {tech['label']} technique structure precisely
- Make it immediately usable — copy-paste ready

Generate the prompt now:"""

        messages = [{"role": "user", "content": synthesis_prompt}]
        yield from self.client.chat_stream(messages)

    # ── Technique instruction builders ─────────────────────────────────────────
    def _technique_instructions(self) -> str:
        instructions = {
            "zero_shot": (
                "Write a direct, clear instruction with no examples. "
                "Include all context, constraints, and desired output format in a single well-structured paragraph or short list."
            ),
            "one_shot": (
                "Structure the prompt as:\n"
                "1. Brief context/instruction\n"
                "2. ONE example: Input -> Output\n"
                "3. The actual task\n"
                "The example must be highly relevant to what the user described."
            ),
            "few_shot": (
                "Structure the prompt as:\n"
                "1. Brief instruction/context\n"
                "2. Example 1: Input -> Output\n"
                "3. Example 2: Input -> Output\n"
                "4. Example 3: Input -> Output (if relevant)\n"
                "5. The actual task\n"
                "Examples must closely match the user's style and domain."
            ),
            "instructional": (
                "Structure as numbered step-by-step directives:\n"
                "- Start with role/context sentence\n"
                "- Number every instruction (1, 2, 3...)\n"
                "- End with explicit output format requirements\n"
                "- Be specific about constraints per step"
            ),
            "persona": (
                "Begin with a strong persona assignment: 'You are [specific expert title with credentials]...'\n"
                "Include: expertise area, communication style, what they prioritize, what they avoid.\n"
                "Then give the task framed for that persona."
            ),
            "json_output": (
                "Structure the prompt to demand JSON output:\n"
                "1. Task description\n"
                "2. Specify exact JSON schema with field names, types, and descriptions\n"
                "3. Add constraint: 'Respond ONLY with valid JSON. No markdown, no explanation.'\n"
                "4. Include a brief JSON example structure."
            ),
            "chain_of_thought": (
                "Include explicit reasoning instructions:\n"
                "- Add 'Think step by step' or 'Let's reason through this carefully'\n"
                "- Ask for intermediate reasoning before the final answer\n"
                "- Structure: Context -> 'First, think about...' -> 'Then consider...' -> 'Finally, output...'"
            ),
            "negative": (
                "Structure as two clear sections:\n"
                "POSITIVE: Detailed description of what IS wanted\n"
                "NEGATIVE: Explicit comma-separated list of what is NOT wanted (prefixed with 'Avoid:', 'Exclude:', or 'Negative prompt:')\n"
                "For image/video: follow standard negative prompting conventions (e.g., 'blurry, watermark, low quality...')"
            ),
        }
        return instructions.get(self.technique, "Apply the technique thoughtfully based on the domain and user needs.")

    def _build_examples_block(self) -> str:
        if self.technique == "few_shot":
            examples = self.examples.get("few_shot_examples", {}).get(self.domain, [])
            if examples:
                block = "BUILT-IN EXAMPLES TO ADAPT (use as style reference, adapt to user's specific request):\n"
                for i, ex in enumerate(examples[:3], 1):
                    block += f"\nExample {i}:\nInput: {ex['user']}\nOutput: {ex['assistant']}\n"
                return block
        elif self.technique == "one_shot":
            example = self.examples.get("one_shot_examples", {}).get(self.domain)
            if example:
                return (
                    f"BUILT-IN EXAMPLE TO ADAPT:\n"
                    f"Input: {example['user']}\n"
                    f"Output: {example['assistant']}\n"
                )
        return ""

    def _format_history(self, history: list[dict]) -> str:
        lines = []
        for msg in history:
            role = "USER" if msg["role"] == "user" else "ASSISTANT"
            lines.append(f"[{role}]: {msg['content']}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 3 — SESSION MANAGER (with streaming output)
# ══════════════════════════════════════════════════════════════════════════════

class SessionManager:
    """Manages the discovery chat loop, commands, and output."""

    COMMANDS = {
        "/generate":  "Synthesize and output the final prompt",
        "/copy":      "Copy the last generated prompt to clipboard",
        "/save":      "Save the last generated prompt to file",
        "/technique": "Switch prompting technique (e.g. /technique persona)",
        "/menu":      "Return to main menu (select new domain/technique)",
        "/config":    "Re-run setup wizard and update saved config",
        "/clear":     "Reset conversation history",
        "/status":    "Show current session info",
        "/stream":    "Toggle streaming output on/off",
        "/wizard":    "Create a new custom domain",
        "/plugins":   "Show loaded plugins and example packs",
        "/help":      "Show available commands",
        "/quit":      "Exit PromptCraft",
    }

    # Signal that the user wants to return to the main menu
    _RETURN_TO_MENU = False

    def __init__(self, client: BackendClient, engine: PromptEngine, domain: str, technique: str):
        self.client        = client
        self.engine        = engine
        self.domain        = domain
        self.technique     = technique
        self.history: list[dict] = []
        self.last_prompt: Optional[str] = None
        self.turn_count    = 0
        self.domain_answers: dict[str, str] = {}  # filled by _ask_domain_questions()
        self.streaming_enabled = True  # Feature 3: streaming on by default

    # ── Main chat loop ─────────────────────────────────────────────────────────
    def run(self) -> None:
        self._init_conversation()

        while True:
            try:
                user_input = console.input("\n[bold bright_white] You > [/]").strip()
            except (EOFError, KeyboardInterrupt):
                self._quit()
                return

            if not user_input:
                continue

            # Command routing
            if user_input.startswith("/"):
                should_quit = self._handle_command(user_input)
                if should_quit:
                    return
                continue

            # Normal chat turn
            self._chat_turn(user_input)

    # ── Domain-specific fixed questions ─────────────────────────────────────────
    def _ask_domain_questions(self) -> dict[str, str]:
        """Ask the user domain-specific fixed questions (image size, video duration, etc.)."""
        questions = DOMAIN_QUESTIONS.get(self.domain, [])
        if not questions:
            return {}

        domain_info = DOMAINS.get(self.domain, {"icon": "⚡", "label": self.domain})
        console.print()
        console.print(Panel(
            f"[bold]Let's nail down some {domain_info.get('label', self.domain)} specifics first.[/]\n"
            f"[muted]These details help craft a much more precise prompt.[/]",
            title=f"{domain_info.get('icon', '⚡')} [domain]{domain_info.get('label', self.domain)} Quick Setup[/]",
            border_style="cyan",
            padding=(1, 2),
        ))
        console.print()

        answers: dict[str, str] = {}
        try:
            for q in questions:
                if "choices" in q and q["choices"]:
                    # Multiple choice question — render a numbered table
                    console.print(f"  [info]{q['prompt']}[/]")
                    for i, choice in enumerate(q["choices"], 1):
                        console.print(f"    [muted]{i}.[/] {choice}")
                    choices_str = [str(i) for i in range(1, len(q["choices"]) + 1)]
                    default_val = q.get("default", "1")
                    answer_idx  = Prompt.ask(
                        f"  [info]Select[/]",
                        choices=choices_str,
                        default=default_val,
                    )
                    answers[q["key"]] = q["choices"][int(answer_idx) - 1]
                else:
                    # Free-text question — sanitize input
                    answer = Prompt.ask(f"  [info]{q['prompt']}[/]", default="")
                    answers[q["key"]] = _sanitize_string(answer) if answer else "Not specified"
                console.print()
        except KeyboardInterrupt:
            console.print()
            console.print(Panel(
                "[muted]Setup interrupted. No worries — see you next time![/]",
                title="[warning]Goodbye[/]",
                border_style="yellow",
                padding=(1, 2),
            ))
            sys.exit(0)

        return answers

    # ── Initialise conversation ────────────────────────────────────────────────
    def _init_conversation(self) -> None:
        # 1. Ask domain-specific fixed questions
        self.domain_answers = self._ask_domain_questions()

        # 2. Build system prompt with domain answers baked in
        system_prompt = self.engine.discovery_system_prompt(self.domain_answers)
        # LM Studio requires strict user/assistant alternation.
        # We must include a user message after the system prompt so the
        # message sequence is: system -> user -> (assistant responds)
        self.history  = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hello! I'm ready to craft a prompt. Please start by asking me your first question."},
        ]

        console.print(Rule("[muted]Session Started[/]", style="dim"))
        console.print()

        with console.status("[cyan]PromptCraft is thinking...[/]", spinner="dots"):
            try:
                greeting = self.client.chat(self.history)
            except ConnectionError as e:
                console.print(f"[error]Connection error: {e}[/]")
                sys.exit(1)

        self.history.append({"role": "assistant", "content": greeting})
        self._print_bot(greeting)

    # ── Single chat turn (with streaming support) ──────────────────────────────
    def _chat_turn(self, user_input: str) -> None:
        # Sanitize user input
        user_input = _sanitize_string(user_input, max_len=_MAX_MESSAGE_LENGTH)
        self.history.append({"role": "user", "content": user_input})
        self.turn_count += 1

        # Trim history if it's grown too long (keep system + last N messages)
        if len(self.history) > _MAX_HISTORY_LENGTH:
            system_msgs = [m for m in self.history if m["role"] == "system"]
            chat_msgs   = [m for m in self.history if m["role"] != "system"]
            # Keep the most recent messages
            chat_msgs = chat_msgs[-(_MAX_HISTORY_LENGTH - len(system_msgs)):]
            self.history = system_msgs + chat_msgs

        # Feature 3: Stream or non-stream based on toggle
        if self.streaming_enabled:
            reply = self._stream_response()
        else:
            with console.status("[cyan]Thinking...[/]", spinner="dots"):
                try:
                    reply = self.client.chat(self.history)
                except ConnectionError as e:
                    console.print(f"[error]{e}[/]")
                    return
                except Exception as e:
                    console.print(f"[error]Unexpected error: {e}[/]")
                    return

        self.history.append({"role": "assistant", "content": reply})
        self._print_bot(reply)

    def _stream_response(self) -> str:
        """Stream the LLM response and return the full text. Falls back to non-streaming on error."""
        collected = []
        try:
            console.print()
            console.print(Panel(
                "[stream]Streaming response...[/]",
                title="[bot]PromptCraft[/]",
                border_style="cyan",
                padding=(0, 1),
            ))

            # Use Live for smooth streaming output
            stream_text = ""
            with Live(console=console, refresh_per_second=15, vertical_overflow="visible") as live:
                for token in self.client.chat_stream(self.history):
                    collected.append(token)
                    stream_text += token
                    live.update(Panel(
                        Markdown(stream_text),
                        title="[bot]PromptCraft[/]",
                        subtitle="[muted]/generate when ready | /stream to toggle",
                        border_style="cyan",
                        padding=(0, 1),
                    ))

            return "".join(collected).strip()

        except (ConnectionError, Exception) as e:
            # Fallback to non-streaming
            if collected:
                # We already got some data, return what we have
                return "".join(collected).strip()
            console.print(f"[warning]Streaming failed, falling back to non-streaming: {e}[/]")
            with console.status("[cyan]Thinking (non-streaming)...[/]", spinner="dots"):
                try:
                    return self.client.chat(self.history)
                except ConnectionError as e2:
                    console.print(f"[error]{e2}[/]")
                    return ""

    # ── Command handler ────────────────────────────────────────────────────────
    def _handle_command(self, raw: str) -> bool:
        """Returns True if session should end."""
        parts  = raw.strip().split(maxsplit=1)
        cmd    = parts[0].lower()
        arg    = parts[1] if len(parts) > 1 else ""

        if cmd == "/generate":
            self._do_generate()
        elif cmd == "/copy":
            self._do_copy()
        elif cmd == "/save":
            self._do_save()
        elif cmd == "/technique":
            self._do_switch_technique(arg)
        elif cmd == "/menu":
            self._do_return_to_menu()
            return True  # End this session; main loop will restart
        elif cmd == "/config":
            self._do_config()
        elif cmd == "/clear":
            self._do_clear()
        elif cmd == "/status":
            self._do_status()
        elif cmd == "/stream":
            self._do_toggle_stream()
        elif cmd == "/wizard":
            self._do_wizard()
        elif cmd == "/plugins":
            self._do_plugins()
        elif cmd == "/help":
            self._do_help()
        elif cmd == "/quit":
            self._quit()
            return True
        else:
            console.print(f"[warning]Unknown command: {cmd}. Type /help for available commands.[/]")

        return False

    # ── /generate (with streaming support) ─────────────────────────────────────
    def _do_generate(self) -> None:
        if self.turn_count == 0:
            console.print("[warning]Have a conversation first! I need to understand your needs.[/]")
            return

        console.print()
        console.print(Rule("[prompt]Synthesizing your prompt...[/]", style="magenta"))

        # Feature 3: Stream the synthesis if enabled
        if self.streaming_enabled:
            prompt = self._do_generate_stream()
        else:
            with console.status(
                f"[magenta]Applying [bold]{TECHNIQUES[self.technique]['label']}[/] technique...[/]",
                spinner="aesthetic",
            ):
                try:
                    chat_history = [m for m in self.history if m["role"] != "system"]
                    prompt = self.engine.synthesize(chat_history, self.domain_answers)
                except ConnectionError as e:
                    console.print(f"[error]{e}[/]")
                    return

        self.last_prompt = prompt
        self._display_final_prompt(prompt)

        # Offer quick actions after generation
        self._post_generate_actions()

    def _do_generate_stream(self) -> str:
        """Generate the prompt with streaming output."""
        collected = []
        try:
            chat_history = [m for m in self.history if m["role"] != "system"]
            domain_info = DOMAINS.get(self.domain, {"icon": "⚡", "label": self.domain})
            tech_info   = TECHNIQUES[self.technique]

            console.print()
            stream_text = ""
            with Live(console=console, refresh_per_second=15, vertical_overflow="visible") as live:
                for token in self.engine.synthesize_stream(chat_history, self.domain_answers):
                    collected.append(token)
                    stream_text += token
                    live.update(Panel(
                        Markdown(stream_text),
                        title=f"{domain_info.get('icon', '⚡')} [bold {domain_info.get('color', 'white')}]{domain_info.get('label', self.domain)}[/]  |  "
                              f"{tech_info['icon']} [tech]{tech_info['label']}[/]",
                        subtitle="[muted]Streaming...[/]",
                        border_style="magenta",
                        padding=(1, 2),
                    ))

            return "".join(collected).strip()

        except (ConnectionError, Exception) as e:
            if collected:
                return "".join(collected).strip()
            console.print(f"[warning]Streaming synthesis failed, falling back: {e}[/]")
            with console.status("[magenta]Generating (non-streaming)...[/]", spinner="aesthetic"):
                chat_history = [m for m in self.history if m["role"] != "system"]
                return self.engine.synthesize(chat_history, self.domain_answers)

    def _display_final_prompt(self, prompt: str) -> None:
        domain_info = DOMAINS.get(self.domain, {"icon": "⚡", "label": self.domain, "color": "white"})
        tech_info   = TECHNIQUES[self.technique]

        console.print()
        console.print(
            Panel(
                prompt,
                title=f"{domain_info.get('icon', '⚡')} [bold {domain_info.get('color', 'white')}]{domain_info.get('label', self.domain)}[/]  |  "
                      f"{tech_info['icon']} [tech]{tech_info['label']}[/]",
                subtitle="[muted]/copy | /save | /generate to regenerate | /menu to start over | continue chatting to refine[/]",
                border_style="magenta",
                padding=(1, 2),
            )
        )
        console.print()

    # ── Post-generate actions ──────────────────────────────────────────────────
    def _post_generate_actions(self) -> None:
        """Show a quick-action menu after prompt generation."""
        console.print(
            Panel(
                "[1] [success]Copy to clipboard[/]\n"
                "[2] [info]Save to file[/]\n"
                "[3] [warning]Return to main menu[/] (new domain/technique)\n"
                "[4] [muted]Continue chatting[/] (refine this prompt)",
                title="[bold]What next?[/]",
                border_style="green",
                padding=(1, 2),
            )
        )
        try:
            choice = Prompt.ask(
                "[info]Choose an action[/]",
                choices=["1", "2", "3", "4"],
                default="1",
            )
        except (EOFError, KeyboardInterrupt):
            return

        if choice == "1":
            self._do_copy()
        elif choice == "2":
            self._do_save()
        elif choice == "3":
            self._do_return_to_menu()
        # choice == "4": just continue the chat loop

    # ── /copy ──────────────────────────────────────────────────────────────────
    def _do_copy(self) -> None:
        """Copy the last generated prompt to the system clipboard."""
        if not self.last_prompt:
            console.print("[warning]Nothing to copy yet. Use /generate first.[/]")
            return

        if _copy_to_clipboard(self.last_prompt):
            console.print("[success]Prompt copied to clipboard![/]")
        else:
            console.print("[warning]Could not copy to clipboard. Install 'pyperclip' (pip install pyperclip) or 'xclip'/'xsel' on Linux.[/]")
            console.print("[muted]You can still use /save to save it to a file.[/]")

    # ── /menu (return to domain/technique selection) ───────────────────────────
    def _do_return_to_menu(self) -> None:
        """Signal that the user wants to return to the main menu."""
        SessionManager._RETURN_TO_MENU = True
        console.print()
        console.print(Panel(
            "[info]Returning to main menu...[/]\n"
            "[muted]Select a new domain and technique for your next prompt.[/]",
            title="[info]Main Menu[/]",
            border_style="cyan",
            padding=(1, 2),
        ))

    # ── /save ──────────────────────────────────────────────────────────────────
    def _do_save(self) -> None:
        if not self.last_prompt:
            console.print("[warning]Nothing to save yet. Use /generate first.[/]")
            return

        try:
            PROMPTS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Use a safe filename — no user input in path
            filename = PROMPTS_DIR / f"{self.domain}_{timestamp}.txt"
            filename = _safe_resolve_path(filename)
        except ValueError as e:
            console.print(f"[error]Unsafe file path: {e}[/]")
            return

        # Assemble file content
        chat_history = [m for m in self.history if m["role"] != "system"]
        conversation_text = "\n".join(
            f"[{m['role'].upper()}]: {m['content']}" for m in chat_history
        )

        # Build domain specs section
        specs_text = ""
        if self.domain_answers:
            specs_lines = [f"  {k.replace('_', ' ').title()}: {v}" for k, v in self.domain_answers.items()]
            specs_text = (
                f"DOMAIN SPECS\n"
                f"{'-'*60}\n"
                + "\n".join(specs_lines) + "\n\n"
            )

        domain_info = DOMAINS.get(self.domain, {"label": self.domain})

        content = (
            f"PromptCraft Export\n"
            f"{'='*60}\n"
            f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Domain    : {domain_info.get('label', self.domain)}\n"
            f"Technique : {TECHNIQUES[self.technique]['label']}\n"
            f"Model     : {self.client.model}\n"
            f"Backend   : {self.client.backend['name']}\n"
            f"Streaming : {'On' if self.streaming_enabled else 'Off'}\n"
            f"Plugins   : {', '.join(plugin_manager.plugins.keys()) if plugin_manager.plugins else 'None'}\n"
            f"{'='*60}\n\n"
            f"FINAL PROMPT\n"
            f"{'-'*60}\n"
            f"{self.last_prompt}\n\n"
            f"{'='*60}\n"
            f"{specs_text}"
            f"DISCOVERY CONVERSATION\n"
            f"{'-'*60}\n"
            f"{conversation_text}\n"
        )

        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)

        console.print(f"[success]Saved to: {filename}[/]")

    # ── /technique ─────────────────────────────────────────────────────────────
    def _do_switch_technique(self, arg: str) -> None:
        if not arg:
            # Show available techniques
            _render_techniques_table(self.domain)
            return

        key = arg.lower().replace("-", "_").replace(" ", "_")
        if key not in TECHNIQUES:
            # Fuzzy match
            matches = [k for k in TECHNIQUES if arg.lower() in k or arg.lower() in TECHNIQUES[k]["label"].lower()]
            if len(matches) == 1:
                key = matches[0]
            else:
                console.print(f"[warning]Unknown technique '{arg}'. Try: {', '.join(TECHNIQUES.keys())}[/]")
                return

        self.technique = key
        self.engine.technique = key
        console.print(f"[success]Switched to: {TECHNIQUES[key]['icon']} {TECHNIQUES[key]['label']}[/]")

    # ── /config ─────────────────────────────────────────────────────────────────
    def _do_config(self) -> None:
        """Re-run the setup wizard and save new config."""
        console.print()
        console.print(Rule("[info]Re-running Setup Wizard[/]", style="cyan"))
        confirm = Confirm.ask("[warning]Reset backend/model config and re-run setup wizard?[/]", default=False)
        if not confirm:
            console.print("[muted]Cancelled.[/]")
            return

        # Delete existing config and restart wizard
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            console.print("[success]Old config removed.[/]")

        console.print("[info]Please restart PromptCraft to run the setup wizard again.[/]")
        console.print("[muted]  python promptcraft.py[/]")

    # ── /clear ─────────────────────────────────────────────────────────────────
    def _do_clear(self) -> None:
        confirm = Confirm.ask("[warning]Clear conversation history and start fresh?[/]")
        if confirm:
            self._init_conversation()
            self.turn_count   = 0
            self.last_prompt  = None
            console.print("[success]Conversation cleared.[/]")

    # ── /status ────────────────────────────────────────────────────────────────
    def _do_status(self) -> None:
        domain_info = DOMAINS.get(self.domain, {"icon": "⚡", "label": self.domain, "color": "white"})
        tech_info   = TECHNIQUES[self.technique]

        table = Table(show_header=False, box=ROUNDED, padding=(0, 2))
        table.add_column("Key",   style="muted", width=14)
        table.add_column("Value", style="bold", width=40)
        table.add_row("Backend",   self.client.backend["name"])
        table.add_row("Model",     self.client.model)
        table.add_row("Domain",    f"{domain_info.get('icon', '⚡')} {domain_info.get('label', self.domain)}")
        table.add_row("Technique", f"{tech_info['icon']} {tech_info['label']}")
        table.add_row("Turns",     str(self.turn_count))
        table.add_row("Saved",     "Yes" if self.last_prompt else "No")
        table.add_row("Streaming", "[success]On[/]" if self.streaming_enabled else "[muted]Off[/]")
        table.add_row("Plugins",   ", ".join(plugin_manager.plugins.keys()) if plugin_manager.plugins else "None")

        # Add domain specs if available
        if self.domain_answers:
            table.add_row("-" * 20, "-" * 30)
            for key, value in self.domain_answers.items():
                pretty_key = key.replace("_", " ").title()
                table.add_row(pretty_key, value)

        console.print(Panel(table, title="[info]Session Status[/]", border_style="cyan"))

    # ── /stream (Feature 3: toggle streaming) ──────────────────────────────────
    def _do_toggle_stream(self) -> None:
        """Toggle streaming output on/off."""
        self.streaming_enabled = not self.streaming_enabled
        state = "[success]ON[/]" if self.streaming_enabled else "[muted]OFF[/]"
        console.print(f"[info]Streaming output: {state}[/]")
        if self.streaming_enabled:
            console.print("[muted]LLM responses will be streamed token-by-token in real time.[/]")
        else:
            console.print("[muted]LLM responses will wait for the full reply before displaying.[/]")

    # ── /wizard (Feature 1: custom domain creation) ────────────────────────────
    def _do_wizard(self) -> None:
        """Launch the custom domain wizard from within a session."""
        new_key = _custom_domain_wizard()
        if new_key:
            console.print(f"[success]Domain '{new_key}' is now available. Use /menu to start a new session with it.[/]")

    # ── /plugins (Feature 2: show plugin info) ─────────────────────────────────
    def _do_plugins(self) -> None:
        """Display plugins matching the current domain and technique.

        Use '/plugins all' to show every loaded plugin regardless of session.
        """
        # Check for 'all' argument
        show_all = False
        # We need to peek at the next input or check if the user typed '/plugins all'
        # Since we don't have args here, we'll prompt the user
        console.print()
        console.print("[info]Show plugins matching your session, or all?[/]")
        choice = Prompt.ask(
            "  [muted][1][/][white] Matching current domain and technique[/]\n"
            "  [muted][2][/][white] All loaded plugins[/]",
            choices=["1", "2"],
            default="1",
        )
        if choice == "2":
            show_all = True

        if show_all:
            plugin_manager.list_plugins()
        else:
            plugin_manager.list_plugins(domain=self.domain, technique=self.technique)

    # ── /help ──────────────────────────────────────────────────────────────────
    def _do_help(self) -> None:
        table = Table(show_header=False, box=ROUNDED, padding=(0, 2))
        table.add_column("Command", style="cmd", no_wrap=True, width=14)
        table.add_column("Description", style="white", width=46)
        for cmd, desc in self.COMMANDS.items():
            table.add_row(cmd, desc)
        console.print(Panel(table, title="[info]Available Commands[/]", border_style="yellow"))

    # ── /quit ──────────────────────────────────────────────────────────────────
    def _quit(self) -> None:
        console.print()
        console.print(Panel(
            "[muted]Thanks for using PromptCraft! Happy prompting.[/]",
            border_style="dim",
        ))

    # ── Display helpers ────────────────────────────────────────────────────────
    def _print_bot(self, text: str) -> None:
        console.print()
        console.print(Panel(
            Markdown(text),
            title="[bot]PromptCraft[/]",
            subtitle="[muted]/generate - to generate . /plugins - to show your plugins| /help",
            border_style="cyan",
            padding=(0, 1),
        ))


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG FILE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _load_config() -> Optional[dict]:
    """Load config from file. Returns None if not found or invalid.

    Only backend + model are persisted; domain and technique are chosen
    fresh every session so the user always picks what they need.
    """
    if not CONFIG_FILE.exists():
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = f.read(_CONFIG_MAX_SIZE)
        config = json.loads(raw)
        # Validate required keys exist (only backend + model now)
        required = ["backend", "model"]
        if not all(k in config for k in required):
            return None
        # Validate values
        if config["backend"] not in BACKENDS:
            return None
        # Validate model name is a safe string
        if not isinstance(config["model"], str) or len(config["model"]) > 256:
            return None
        return config
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return None


def _save_config(backend: str, model: str) -> None:
    """Save config to file. Only backend + model are persisted.

    Domain, technique, and domain_answers are intentionally NOT saved
    so the user must choose them fresh every session.
    """
    config = {
        "version": VERSION,
        "backend": backend,
        "model": model,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        # Write atomically: write to temp file then rename
        tmp_path = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        tmp_path.replace(CONFIG_FILE)
    except OSError as e:
        console.print(f"[warning]Could not save config: {e}[/]")


def _show_config_summary(config: dict) -> None:
    """Display the loaded config (backend + model only)."""
    backend_info = BACKENDS.get(config["backend"], {})

    table = Table(show_header=False, box=ROUNDED, padding=(0, 2))
    table.add_column("Key",   style="muted", width=14)
    table.add_column("Value", style="bold", width=40)
    table.add_row("Backend",   backend_info.get("name", config["backend"]))
    table.add_row("Model",     config["model"])
    table.add_row("Config",    str(CONFIG_FILE))
    table.add_row("", "[muted]Domain and technique will be selected next...[/]")

    console.print(Panel(
        table,
        title="[success]Config Found — Resuming Session[/]",
        border_style="green",
        padding=(1, 2),
    ))


def _quick_start_from_config(config: dict) -> Optional[tuple]:
    """Build session from saved config. Only backend+model are restored;
    domain and technique are always asked fresh.
    """
    backend_key = config["backend"]
    model       = config["model"]

    # Check if the saved backend is reachable
    if not BackendClient.ping(backend_key):
        console.print(f"[warning]Saved backend '{BACKENDS[backend_key]['name']}' is not reachable.[/]")
        if Confirm.ask("[info]Re-run setup wizard?[/]", default=True):
            return None  # Signal to fall through to full wizard
        console.print("[muted]Continuing with saved config anyway...[/]")

    # Verify the saved model still exists
    available = BackendClient.list_models(backend_key)
    if available and model not in available:
        console.print(f"[warning]Saved model '{model}' not found. Available: {', '.join(available)}[/]")
        if Confirm.ask("[info]Pick a new model?[/]", default=True):
            model = _select_model(backend_key)
            # Update saved config with new model
            _save_config(backend_key, model)
        else:
            console.print("[muted]Will try with saved model name anyway...[/]")

    # Always ask domain + technique fresh (these are NOT saved)
    console.print()
    console.print(Rule("[info]Domain & Technique Selection[/]", style="cyan"))
    console.print("[muted]Choose your domain and technique for this session.[/]")
    domain    = _select_domain()
    technique = _select_technique_simple(domain)

    client = BackendClient(backend_key, model)
    engine = PromptEngine(client, domain, technique)
    session = SessionManager(client, engine, domain, technique)
    return client, engine, session


def _select_technique_simple(domain: str) -> str:
    """Quick technique picker without LLM suggestion (for config re-use)."""
    console.print()
    console.print(Rule("[info]Technique Selection[/]", style="cyan"))
    _render_techniques_table(domain)
    console.print()

    tech_keys = list(TECHNIQUES.keys())
    choices   = [str(i) for i in range(1, len(tech_keys) + 1)]
    recs = DOMAIN_TECHNIQUE_RECOMMENDATIONS.get(domain, tech_keys)
    default = str(tech_keys.index(recs[0]) + 1) if recs[0] in tech_keys else "1"
    choice  = Prompt.ask("[info]Select technique[/]", choices=choices, default=default)
    return tech_keys[int(choice) - 1]


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 5 — CLI SHELL / WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def _render_banner() -> None:
    banner = Text()
    banner.append("  ██████╗ ██████╗  ██████╗ ███╗   ███╗██████╗ ████████╗\n", style="bold magenta")
    banner.append(" ██╔══██╗██╔══██╗██╔═══██╗████╗ ████║██╔══██╗╚══██╔══╝\n", style="bold magenta")
    banner.append(" ██████╔╝██████╔╝██║   ██║██╔████╔██║██████╔╝   ██║   \n", style="bold bright_magenta")
    banner.append(" ██╔═══╝ ██╔══██╗██║   ██║██║╚██╔╝██║██╔═══╝    ██║   \n", style="bold cyan")
    banner.append(" ██║     ██║  ██║╚██████╔╝██║ ╚═╝ ██║██║        ██║   \n", style="bold cyan")
    banner.append(" ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚═╝        ╚═╝   \n", style="bold bright_cyan")
    banner.append("                      ✦ CRAFT \n", style="bold white")
    subtitle = Text(f"  Prompt Engineering CLI  |  v{VERSION}  |  Local LLM Powered  |  Streaming + Plugins", style="dim")

    console.print()
    console.print(Align.center(banner))
    console.print(Align.center(subtitle))
    console.print()


def _select_backend() -> str:
    """Ping both backends, let user pick."""
    console.print(Rule("[info]Backend Selection[/]", style="cyan"))
    console.print()

    statuses = {}
    with console.status("[cyan]Detecting local backends...[/]", spinner="dots"):
        for key in BACKENDS:
            statuses[key] = BackendClient.ping(key)

    table = Table(show_header=True, header_style="bold cyan", box=ROUNDED, padding=(0, 2))
    table.add_column("#",       style="bold", width=4, justify="center")
    table.add_column("Backend", style="bold white", width=16)
    table.add_column("URL",     style="muted", width=30)
    table.add_column("Status",  justify="center", width=14)

    items = list(BACKENDS.items())
    for i, (key, info) in enumerate(items, 1):
        status = "[success]Online[/]" if statuses[key] else "[error]Offline[/]"
        table.add_row(str(i), info["name"], info["base_url"], status)

    console.print(table)
    console.print()

    choices = [str(i) for i in range(1, len(items) + 1)]
    while True:
        choice = Prompt.ask("[info]Select backend[/]", choices=choices, default="1")
        key = items[int(choice) - 1][0]
        if not statuses[key]:
            console.print(f"[warning]{BACKENDS[key]['name']} appears offline. Continue anyway?[/]")
            if not Confirm.ask("Proceed?", default=False):
                continue
        return key


def _select_model(backend_key: str) -> str:
    """Fetch available models and let user pick."""
    console.print()
    console.print(Rule("[info]Model Selection[/]", style="cyan"))
    console.print()

    with console.status("[cyan]Fetching available models...[/]", spinner="dots"):
        models = BackendClient.list_models(backend_key)

    if not models:
        console.print("[warning]No models found automatically.[/]")
        return Prompt.ask("[info]Enter model name manually[/]", default="llama3.2")

    table = Table(show_header=True, header_style="bold cyan", box=ROUNDED, padding=(0, 2))
    table.add_column("#",     style="bold", width=4, justify="center")
    table.add_column("Model", style="bold white", width=40)

    for i, m in enumerate(models, 1):
        table.add_row(str(i), m)

    console.print(table)
    console.print()

    choices = [str(i) for i in range(1, len(models) + 1)]
    choice  = Prompt.ask("[info]Select model[/]", choices=choices, default="1")
    return models[int(choice) - 1]


def _select_domain() -> str:
    """Let user pick a domain."""
    console.print()
    console.print(Rule("[info]Domain Selection[/]", style="cyan"))
    console.print("[muted]What kind of prompt are you building?[/]")
    console.print()

    items = list(DOMAINS.items())
    table = Table(show_header=True, header_style="bold cyan", box=ROUNDED, padding=(0, 2))
    table.add_column("#",      style="bold", width=4, justify="center")
    table.add_column("Domain", style="bold white", width=30)

    for i, (key, info) in enumerate(items, 1):
        table.add_row(str(i), f"{info['icon']}  {info['label']}")

    console.print(table)
    console.print()

    choices = [str(i) for i in range(1, len(items) + 1)]
    choice  = Prompt.ask("[info]Select domain[/]", choices=choices, default="1")
    return items[int(choice) - 1][0]


def _render_techniques_table(domain: str) -> None:
    recs   = DOMAIN_TECHNIQUE_RECOMMENDATIONS.get(domain, list(TECHNIQUES.keys()))
    items  = list(TECHNIQUES.items())

    table = Table(show_header=True, header_style="bold magenta", box=ROUNDED, padding=(0, 2))
    table.add_column("#",          style="bold",  width=4, justify="center")
    table.add_column("Technique",  style="bold white", width=22, no_wrap=True)
    table.add_column("Description",style="white", width=48)
    table.add_column("Fit",        justify="center", width=16)

    for i, (key, info) in enumerate(items, 1):
        fit = "[success]Recommended[/]" if key in recs[:2] else (
              "[info]Good fit[/]"       if key in recs      else "[muted]--[/]"
        )
        table.add_row(str(i), f"{info['icon']} {info['label']}", info["desc"], fit)

    console.print(table)


def _select_technique(client: BackendClient, engine: PromptEngine, domain: str) -> str:
    """LLM suggests a technique, user confirms or overrides."""
    console.print()
    console.print(Rule("[info]Technique Selection[/]", style="cyan"))
    console.print()

    # LLM suggestion
    with console.status("[magenta]Asking LLM for best technique recommendation...[/]", spinner="dots"):
        try:
            suggestion_key = engine.suggest_technique()
        except Exception:
            suggestion_key = DOMAIN_TECHNIQUE_RECOMMENDATIONS.get(domain, ["zero_shot"])[0]

    suggestion = TECHNIQUES[suggestion_key]
    console.print(Panel(
        f"{suggestion['icon']}  [tech]{suggestion['label']}[/]\n\n"
        f"[white]{suggestion['desc']}[/]\n\n"
        f"[muted]Recommended for: {', '.join(suggestion['best_for'])}[/]",
        title="[magenta]LLM Recommendation[/]",
        border_style="magenta",
        padding=(1, 2),
    ))
    console.print()

    if Confirm.ask("[info]Use this technique?[/]", default=True):
        return suggestion_key

    # Manual override
    console.print()
    _render_techniques_table(domain)
    console.print()

    tech_keys = list(TECHNIQUES.keys())
    choices   = [str(i) for i in range(1, len(tech_keys) + 1)]
    choice    = Prompt.ask("[info]Select technique[/]", choices=choices, default="1")
    return tech_keys[int(choice) - 1]


def _startup_wizard(skip_banner: bool = False) -> tuple:
    """Full startup wizard. Returns ready-to-run session.

    Args:
        skip_banner: If True, don't print the banner (it was already shown).
    """
    if not skip_banner:
        _render_banner()

    try:
        # 1. Backend
        backend_key = _select_backend()

        # 2. Model
        model = _select_model(backend_key)
        client = BackendClient(backend_key, model)

        # 3. Domain
        domain = _select_domain()

        # 4. Technique (LLM-assisted)
        temp_engine = PromptEngine(client, domain, "zero_shot")
        technique   = _select_technique(client, temp_engine, domain)

    except KeyboardInterrupt:
        console.print()
        console.print(Panel(
            "[muted]Setup wizard interrupted. No worries — see you next time![/]",
            title="[warning]Goodbye[/]",
            border_style="yellow",
            padding=(1, 2),
        ))
        sys.exit(0)

    # 5. Build final engine + session
    engine  = PromptEngine(client, domain, technique)
    session = SessionManager(client, engine, domain, technique)

    # 6. Save config for next run (only backend + model)
    _save_config(backend_key, model)

    # 7. Pre-session summary
    domain_info = DOMAINS.get(domain, {"icon": "⚡", "label": domain, "color": "white"})
    tech_info   = TECHNIQUES[technique]
    console.print()
    console.print(Panel(
        f"[bold]Backend :[/]   {BACKENDS[backend_key]['name']}\n"
        f"[bold]Model   :[/]   {model}\n"
        f"[bold]Domain  :[/]   {domain_info.get('icon', '⚡')} {domain_info.get('label', domain)}\n"
        f"[bold]Technique:[/]  {tech_info['icon']} {tech_info['label']}\n"
        f"[bold]Streaming:[/]  [success]On[/] (use /stream to toggle)\n"
        f"[bold]Plugins :[/]   {', '.join(plugin_manager.plugins.keys()) if plugin_manager.plugins else 'None'}\n"
        f"[bold]Config  :[/]   Saved to {CONFIG_FILE}\n\n"
        f"[muted]Domain-specific questions will be asked next...[/]\n\n"
        f"[muted]Type [cmd]/help[/] for commands | [cmd]/generate[/] to create your prompt | [cmd]/quit[/] to exit[/]",
        title="[success]Session Ready[/]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()

    return client, engine, session


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE 4 — HEADLESS / QUIET MODE
# ══════════════════════════════════════════════════════════════════════════════

def _run_headless(args: argparse.Namespace) -> None:
    """Headless/quiet mode: skip banner, discovery, and domain questions.

    Accepts all parameters via CLI args, generates the prompt, prints to
    stdout, and exits. Makes PromptCraft pipeable.

    Usage:
        python promptcraft.py --headless --domain image --technique negative --input "sunset over Cairo"
        python promptcraft.py --quiet --domain code --technique chain_of_thought --input "REST API"
        echo "sunset over Cairo" | python promptcraft.py --headless --domain image --technique negative
    """
    # Validate required arguments
    if not args.domain:
        print("Error: --domain is required in headless mode.", file=sys.stderr)
        sys.exit(1)

    if not args.technique:
        print("Error: --technique is required in headless mode.", file=sys.stderr)
        sys.exit(1)

    # Validate domain
    domain_key = args.domain.lower().replace("-", "_").replace(" ", "_")
    if domain_key not in DOMAINS:
        print(f"Error: Unknown domain '{args.domain}'. Available: {', '.join(DOMAINS.keys())}", file=sys.stderr)
        sys.exit(1)

    # Validate technique
    technique_key = args.technique.lower().replace("-", "_").replace(" ", "_")
    if technique_key not in TECHNIQUES:
        print(f"Error: Unknown technique '{args.technique}'. Available: {', '.join(TECHNIQUES.keys())}", file=sys.stderr)
        sys.exit(1)

    # Get input text
    input_text = args.input
    if not input_text:
        # Try reading from stdin (pipe support)
        if not sys.stdin.isatty():
            input_text = sys.stdin.read().strip()
        if not input_text:
            print("Error: --input is required in headless mode (or pipe text via stdin).", file=sys.stderr)
            sys.exit(1)

    input_text = _sanitize_string(input_text, max_len=_MAX_MESSAGE_LENGTH)

    # Determine backend and model
    config = _load_config()
    backend_key = args.backend or (config["backend"] if config else None)
    model = args.model or (config["model"] if config else None)

    if not backend_key:
        # Auto-detect: try Ollama first, then LM Studio
        for key in BACKENDS:
            if BackendClient.ping(key):
                backend_key = key
                break
        if not backend_key:
            print("Error: No backend available. Start Ollama or LM Studio, or use --backend.", file=sys.stderr)
            sys.exit(1)

    if not model:
        models = BackendClient.list_models(backend_key)
        if models:
            model = models[0]
        else:
            model = "llama3.2"

    # Build the client and engine
    client = BackendClient(backend_key, model)
    engine = PromptEngine(client, domain_key, technique_key)

    # Build a minimal conversation history from the input
    # In headless mode, we skip the discovery chat entirely
    # The input text serves as the complete user requirement
    domain_answers = {}
    if args.answers:
        # Parse --answers key=value,key=value pairs
        for pair in args.answers.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                domain_answers[_sanitize_string(k.strip(), max_len=32)] = _sanitize_string(v.strip())

    # Build the synthesis prompt directly (no discovery phase)
    domain_label = DOMAINS[domain_key]["label"]
    tech = TECHNIQUES[technique_key]
    technique_instructions = engine._technique_instructions()
    examples_block = engine._build_examples_block()

    specs_block = ""
    if domain_answers:
        spec_lines = [f"- {k.replace('_', ' ').title()}: {v}" for k, v in domain_answers.items()]
        specs_block = (
            "DOMAIN SPECIFICATIONS (fixed constraints from user):\n"
            + "\n".join(spec_lines) + "\n\n"
            "These are NON-NEGOTIABLE constraints that MUST be reflected in the final prompt.\n\n"
        )

    synthesis_prompt = f"""You are an expert Prompt Engineer. Your task is to synthesize a comprehensive, production-ready prompt.

DOMAIN: {domain_label}
TECHNIQUE: {tech['label']} — {tech['desc']}

{specs_block}USER REQUEST:
{input_text}

TECHNIQUE APPLICATION RULES:
{technique_instructions}

{examples_block}

OUTPUT INSTRUCTIONS:
- Generate ONLY the final engineered prompt, nothing else
- Do NOT include any preamble, explanation, or "Here is your prompt:"
- The prompt must be comprehensive, using all information provided
- Apply the {tech['label']} technique structure precisely
- Make it immediately usable — copy-paste ready

Generate the prompt now:"""

    # Generate and output
    try:
        if args.stream:
            # Stream to stdout (for piping)
            for token in client.chat_stream([{"role": "user", "content": synthesis_prompt}]):
                sys.stdout.write(token)
                sys.stdout.flush()
            sys.stdout.write("\n")
        else:
            # Non-streaming: print complete result
            result = client.chat([{"role": "user", "content": synthesis_prompt}])
            print(result)

    except ConnectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Optionally save to file
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result if not args.stream else "Generated via streaming (output sent to stdout)")
            print(f"Saved to: {output_path}", file=sys.stderr)
        except OSError as e:
            print(f"Error saving file: {e}", file=sys.stderr)

    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="promptcraft",
        description="PromptCraft — Local LLM Prompt Engineering CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python promptcraft.py\n"
            "  python promptcraft.py --setup\n"
            "  python promptcraft.py --list-techniques\n"
            "  python promptcraft.py --list-domains\n"
            "  python promptcraft.py --headless --domain image --technique negative --input \"sunset over Cairo\"\n"
            "  python promptcraft.py --quiet --domain code --technique chain_of_thought --input \"REST API in Python\"\n"
            "  echo \"sunset over Cairo\" | python promptcraft.py --headless --domain image --technique negative\n"
            "  python promptcraft.py --headless --domain writing --technique persona --input \"blog post\" --output prompt.txt\n"
            "  python promptcraft.py --headless --domain image --technique negative --input \"sunset\" --stream\n"
        ),
    )
    parser.add_argument("--version",         action="version", version=f"PromptCraft {VERSION}")
    parser.add_argument("--setup",           action="store_true", help="Force re-run the setup wizard (ignores saved config)")
    parser.add_argument("--list-techniques", action="store_true", help="Show all prompting techniques and exit")
    parser.add_argument("--list-domains",    action="store_true", help="Show all supported domains and exit")

    # Feature 4: Headless / quiet mode flags
    parser.add_argument("--headless",        action="store_true", help="Headless mode: skip banner, discovery chat, and domain questions")
    parser.add_argument("--quiet",           action="store_true", help="Quiet mode: alias for --headless (suppresses all interactive prompts)")
    parser.add_argument("--domain",          type=str, default=None, help="Domain key (e.g. image, code, writing) — required in headless mode")
    parser.add_argument("--technique",       type=str, default=None, help="Technique key (e.g. negative, chain_of_thought) — required in headless mode")
    parser.add_argument("--input",           type=str, default=None, help="Input text / user request — required in headless mode (or pipe via stdin)")
    parser.add_argument("--backend",         type=str, default=None, help="Backend key (ollama or lmstudio) — auto-detected if omitted")
    parser.add_argument("--model",           type=str, default=None, help="Model name — uses config or auto-detected if omitted")
    parser.add_argument("--output",          type=str, default=None, help="Save generated prompt to file (in addition to stdout)")
    parser.add_argument("--stream",          action="store_true", help="Stream output token-by-token to stdout (headless mode)")
    parser.add_argument("--answers",         type=str, default=None, help="Domain-specific answers as key=value pairs (comma-separated), e.g. --answers \"style=Digital art,resolution=1024x1024\"")

    args = parser.parse_args()

    # ── Merge custom domains from file ─────────────────────────────────────────
    _merge_custom_domains()

    # ── Load plugins ───────────────────────────────────────────────────────────
    plugin_manager.load_all()

    # ── Headless / quiet mode ──────────────────────────────────────────────────
    if args.headless or args.quiet:
        _run_headless(args)
        return  # _run_headless calls sys.exit(), but just in case

    # ── List commands ──────────────────────────────────────────────────────────
    if args.list_techniques:
        console.print()
        _render_techniques_table("custom")
        console.print()
        return

    if args.list_domains:
        console.print()
        table = Table(show_header=True, header_style="bold cyan", box=ROUNDED, padding=(0, 2))
        table.add_column("Key",    style="muted", no_wrap=True, width=14)
        table.add_column("Domain", style="bold white", width=30)
        for key, info in DOMAINS.items():
            table.add_row(key, f"{info['icon']} {info['label']}")
        console.print(table)
        console.print()
        return

    # ── Session startup with config check ──────────────────────────────────────
    banner_shown = False
    _render_banner()
    banner_shown = True

    # Load config once at startup (skip if --setup flag is used)
    saved_config = None
    if not args.setup:
        saved_config = _load_config()

    # Main session loop — supports returning to the menu for new domain/technique
    while True:
        client = engine = session = None

        # Try to use saved config for backend/model
        if saved_config:
            _show_config_summary(saved_config)
            result = _quick_start_from_config(saved_config)
            if result is not None:
                client, engine, session = result

        # Fall back to full wizard if no config or quick-start returned None
        if client is None:
            # Banner already shown above — tell wizard to skip it
            client, engine, session = _startup_wizard(skip_banner=banner_shown)

        # Reset the menu-return flag before running
        SessionManager._RETURN_TO_MENU = False

        # Run the session
        try:
            session.run()
        except KeyboardInterrupt:
            console.print()
            console.print(Panel(
                "[muted]Interrupted. Goodbye![/]",
                border_style="yellow",
                padding=(1, 2),
            ))
            sys.exit(0)

        # If the user chose /menu, restart the loop for a new session
        if SessionManager._RETURN_TO_MENU:
            console.print()
            console.print(Rule("[info]Starting New Session[/]", style="cyan"))
            console.print()
            continue

        # Normal exit
        break


if __name__ == "__main__":
    main()
