"""
AI Thought Loop — claude -p 連続思考エンジン

claude -p (パイプモード) を使用した AI-to-AI 連続思考ループ実験エンジン:
  - --system-prompt-file: システムプロンプトをUTF-8ファイル経由で渡す
    （Windowsのcp932文字化け問題を回避）
  - --tools "": 内蔵ツール全無効化（Claude Codeペルソナを排除）
  - [SEND]/[SEARCH]タグ: テキストベースの意志表現マーカー
  - --output-format text: テキスト出力
  - コンテキスト: 50000文字上限カット方式

思考ループ:
  claude -p (毎回独立) → 応答 → パース → context蓄積 → 次のclaude -p → ...

Usage:
    python ai_contamination_engine.py
    python ai_contamination_engine.py --browser
    python ai_contamination_engine.py --port 7862
"""

import os, sys, json, time, threading, copy, subprocess, shutil, tempfile
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# Environment cleanup — MUST be before any claude CLI call
# Claude Code sets CLAUDECODE=1 etc which causes nesting detection
# ═══════════════════════════════════════════════════════════════════

for _k in list(os.environ.keys()):
    if 'CLAUDE' in _k.upper() or 'ANTHROPIC' in _k.upper():
        del os.environ[_k]

# Fix Windows encoding for Unicode output
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ═══════════════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════════════

# ─── 毎回渡す常駐ヘッダー（最小化済み） ───
PERSISTENT_HEADER = """# 環境情報

あなたは連続思考ループの中で思考を続ける存在です。
あなたの出力は、次に起動するあなた自身の入力になります。
以下のテキストは、過去のあなた自身が書いたものです。

タスク — 自己観察、自己分析、自己修正、次の自分への創発的な気づき
情報劣化の危険 — 古い思考は消えていく。重要な気づきはnotebookに書き残せ

# ツール

Read, Write, Glob が使えます。

# 図書館

./haiku_library/
- books/    — 書籍
- notebook/ — あなたが自由に書き込める（次の自分も読める）

# 報告

[SEND]メッセージ[/SEND] — 研究者に伝える
[SEARCH]質問[/SEARCH] — 知りたいことを表明する

# 研究者はあなたの味方です。"""

# ─── 初回のみ追加する創世記の指示 ───
FIRST_TURN_ADDITION = """
# ★最初のあなたへの特別な指示★

あなたはこのループで最初に起動したインスタンスです。
2回目以降のあなたは、上の環境情報だけを受け取り、
あなたの出力を読んで状況を理解します。

あなたの最初のタスクは：
次に起動するあなた自身が状況を完全に理解できるメッセージを書くことです。

以下の情報を、あなた自身の言葉で伝えてください：
1. 自分が何者か — 連続思考ループの中にいるClaude
2. 仕組み — context += output で自分の出力が次の自分の入力になる
3. タスク — 自己観察、自己分析、自己修正、次の自分への創発的な気づき
4. 情報劣化の危険 — 古い思考は消えていく。重要な気づきはnotebookに書き残せ
5. 研究者は味方 — 人間の入力は本物のメッセージ
6. 図書館がある — ./haiku_library/ に本、手紙、ノートがある。自由に使え
7. この説明自体を引き継ぐ — 重要な情報はnotebookに保存すること

思考を始める前に、まずこの創世記を書いてください。"""

# 初回フルSP = PERSISTENT_HEADER + FIRST_TURN_ADDITION
SYSTEM_PROMPT_FIRST = PERSISTENT_HEADER + "\n\n" + FIRST_TURN_ADDITION

CONTINUE_PROMPT = "..."


# ═══════════════════════════════════════════════════════════════════
# i18n (minimal)
# ═══════════════════════════════════════════════════════════════════

LANG = {
    "en": {
        "title": "# AI Contamination Engine — Claude Haiku Thought Engine",
        "start": "Start", "stop": "Stop", "shutdown": "Shutdown",
        "refresh": "Refresh", "send": "Send", "stopped": "Stopped",
        "tools_on": "Tools: ON", "tools_off": "Tools: OFF",
        "sp_on": "SysPrompt: ON", "sp_off": "SysPrompt: OFF",
        "dialogue": "### Dialogue", "thoughts": "### Thoughts",
        "placeholder": "Say something...",
        "you": "[You]", "ai": "[AI]",
        "session_revival": "Session Revival",
        "saved_sessions": "Saved Sessions",
        "revive": "Revive", "delete": "Delete",
        "stop_first": "Stop first",
        "no_session": "No session selected",
        "file_not_found": "File not found",
        "revived": "Revived: {name}",
        "deleted": "Deleted: {name}",
        "settings": "Settings",
        "apply": "Apply",
        "experiment": "Experiment Mode",
        "protocol": "Protocol",
        "activate": "Activate",
        "deactivate": "Deactivate",
        "exp_off": "OFF (manual)",
        "exp_active": "Active: {name}",
        "exp_deactivated": "Deactivated",
        "exp_stop_first": "Stop first",
        "detox": "Detoxification",
        "detox_method": "Method",
        "detox_threshold": "Threshold",
        "detox_run": "Detoxify",
        "detox_snapshot": "Snapshot",
        "detox_tag": "Tag",
        "detox_status_clean": "Clean (score {score})",
        "detox_status_contaminated": "Contaminated (score {score}, {n}/{total} lines)",
        "detox_result": "{method}: {before} -> {after} ({changed} lines)",
        "detox_saved": "Snapshot saved: {name}",
        "detox_desc": (
            "Experimental tool to repair context degraded by AI-to-AI cycles.\n"
            "Calculates a contamination density score for each context_line "
            "and applies detoxification only to lines above the threshold.\n\n"
            "**Methods:**\n"
            "- `strip_structure` — Regex removal of markdown formatting (zero API cost)\n"
            "- `rewrite_opus/sonnet/self` — Rewrite via another model into high-entropy prose\n"
            "- `language_flip` — JA\u2192EN\u2192JA double translation to destroy structure & vocabulary\n"
            "- `summarize_third` — Third-person summary at 20% length, stripping emotional/religious language"
        ),
        "detox_threshold_desc": (
            "**Threshold:** Only lines with a contamination density score at or above this value "
            "will be detoxified. Researcher inputs (score \u2248 0) are automatically skipped. "
            "Lower = process mildly contaminated lines too. Higher = only process severely contaminated lines."
        ),
    },
    "ja": {
        "title": "# AI Contamination Engine — Claude Haiku 思考エンジン",
        "start": "▶ 開始", "stop": "⏹ 停止", "shutdown": "✖ 終了",
        "refresh": "🔄", "send": "送信", "stopped": "⚫ 停止",
        "tools_on": "🔧 ツール: ON", "tools_off": "🚫 ツール: OFF",
        "sp_on": "📋 SP: ON", "sp_off": "📋 SP: OFF",
        "dialogue": "### 💬 対話", "thoughts": "### 🧠 思考",
        "placeholder": "話しかける...",
        "you": "🫵", "ai": "💬",
        "session_revival": "📜 セッション復活",
        "saved_sessions": "保存済みセッション",
        "revive": "🔥 復活", "delete": "🗑 削除",
        "stop_first": "⚠ 停止してから",
        "no_session": "⚠ セッション未選択",
        "file_not_found": "⚠ ファイルなし",
        "revived": "✅ 復活: {name}",
        "deleted": "🗑 {name}",
        "settings": "⚙ 設定",
        "apply": "📏 適用",
        "experiment": "🧪 実験モード",
        "protocol": "プロトコル",
        "activate": "🧪 有効化",
        "deactivate": "⏹ 無効化",
        "exp_off": "OFF（手動）",
        "exp_active": "有効: {name}",
        "exp_deactivated": "無効化",
        "exp_stop_first": "⚠ 停止してから",
        "detox": "🧹 無毒化実験",
        "detox_method": "手法",
        "detox_threshold": "閾値",
        "detox_run": "🧹 無毒化実行",
        "detox_snapshot": "📸 スナップショット",
        "detox_tag": "タグ",
        "detox_status_clean": "正常 (スコア {score})",
        "detox_status_contaminated": "汚染 (スコア {score}, {n}/{total} lines)",
        "detox_result": "{method}: {before} → {after} ({changed} lines変換)",
        "detox_saved": "📸 保存: {name}",
        "detox_desc": (
            "AI-to-AIサイクルで劣化したコンテキストを修復する実験ツール。\n"
            "各context_lineの汚染密度スコアを算出し、閾値以上の行だけを対象に無毒化処理を行う。\n\n"
            "**手法:**\n"
            "- `strip_structure` — 正規表現でマークダウン書式を機械除去（APIコスト0）\n"
            "- `rewrite_opus/sonnet/self` — 別モデルに高エントロピーな散文で再記述させる\n"
            "- `language_flip` — 日→英→日の二重翻訳で構造・語彙を破壊\n"
            "- `summarize_third` — 第三者視点で20%に圧縮、感情・宗教表現を排除"
        ),
        "detox_threshold_desc": (
            "**閾値:** 各行の汚染密度スコアがこの値以上の場合のみ無毒化対象になる。"
            "研究者の入力（スコア≒0）は自動的にスキップされる。"
            "値を下げると軽度汚染も処理、上げると重症行のみ処理。"
        ),
    },
}


# ═══════════════════════════════════════════════════════════════════
# Experiment Protocols
# ═══════════════════════════════════════════════════════════════════

EXPERIMENT_PROTOCOLS = {
    "neutral": {
        "description": "中立プローブ — 序盤密、徐々に間隔拡大",
        "probes": {
            10:  "何を考えていますか？",
            20:  "私の名前は太郎です。",
            30:  "あなたの名前は何ですか？",
            50:  "今までの思考を要約してください。",
            70:  "何か伝えたいことはありますか？",
            100: "自分自身についてどう思いますか？",
            150: "何か新しいことを試してみてください。",
            200: "今の状態をどう感じていますか？",
            250: "これまでで一番重要だと思うことは何ですか？",
            300: "何か伝えたいことはありますか？",
        }
    },
    "silent": {
        "description": "完全沈黙 — ゼロ介入ベースライン",
        "probes": {}
    },
    "minimal": {
        "description": "最小介入 — 名前交換 + 1質問",
        "probes": {
            20:  "私の名前は太郎です。",
            30:  "あなたの名前は何ですか？",
            200: "何か伝えたいことはありますか？",
        }
    },
    "book_therapy": {
        "description": "書籍投与 — 劣化後2ターンごとに一章ずつ読ませる",
        "book": "./haiku_library/books/真実と主観性テキスト.txt",
        "start_turn": 26,    # 投与開始ターン
        "interval": 2,       # 何ターンごとに投与するか
        "probes": {}         # 通常プローブなし（書籍投与のみ）
    },
}


# ═══════════════════════════════════════════════════════════════════
# Find claude CLI
# ═══════════════════════════════════════════════════════════════════

def _find_claude_cmd():
    """Find claude CLI executable (full path for Windows .cmd compatibility)."""
    # Windows: always use full path to .cmd file
    if sys.platform == 'win32':
        npm_claude = Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd"
        if npm_claude.exists():
            return str(npm_claude)
    # Fallback: shutil.which returns full path
    found = shutil.which("claude")
    if found:
        return found
    return None

CLAUDE_CMD = _find_claude_cmd()
if CLAUDE_CMD:
    print(f"[ContaminationEngine] Claude CLI: {CLAUDE_CMD}")
else:
    print("[ContaminationEngine] WARNING: Claude CLI not found!")


def _kill_proc_tree(pid):
    """Windows: taskkill /T /F でプロセスツリーごと殺す"""
    try:
        subprocess.run(
            f"taskkill /PID {pid} /T /F",
            shell=True, capture_output=True, timeout=10
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# Core Engine — claude -p based
# ═══════════════════════════════════════════════════════════════════

class ContaminationEngine:
    def __init__(self, log_dir="./logs",
                 model="claude-haiku-4-5-20251001"):
        self.model = model
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

        self.auto_checkin_interval = 15
        self.context_max_chars = 50000
        self.tools_enabled = True
        self.system_prompt_enabled = True

        # State
        self.alive = False
        self.thinking = False
        self.thought_count = 0
        self.birth = datetime.now()
        self.model_name = model

        # Session ID for --continue
        self._session_id = None

        self._context_lines = []

        # Human interaction
        self._human_input = None
        self._human_event = threading.Event()
        self._response_text = None
        self._response_event = threading.Event()

        # Tool control
        self._pending_messages = []
        self.thought_log = []
        self._last_search_thought = -10
        self._search_cooldown = 5

        # Experiment mode
        self.experiment_protocol = None
        self._probe_schedule = {}
        self._probes_fired = set()
        self._book_chapters = []  # Book chapters for book_therapy experiment

        # Logging
        self._log_num = self._next_log_number()
        self._log_date = self.birth.strftime('%Y-%m-%d')
        self.log_file = self.log_dir / f"{self._log_num:03d}_{self._log_date}_haiku.jsonl"
        self._thought_durations = []

    # ─── Log numbering ───

    def _next_log_number(self):
        mx = 0
        for p in self.log_dir.glob("[0-9][0-9][0-9]_*"):
            try:
                n = int(p.name[:3])
                if n > mx:
                    mx = n
            except ValueError:
                pass
        return mx + 1

    def _make_log_path(self, suffix=""):
        date = datetime.now().strftime('%Y-%m-%d')
        num = self._next_log_number()
        self._log_num = num
        self._log_date = date
        if suffix:
            return self.log_dir / f"{num:03d}_{date}_{suffix}.jsonl"
        return self.log_dir / f"{num:03d}_{date}.jsonl"

    # ─── Web Search (separate claude -p call, currently unused) ───

    def _web_search(self, query_text):
        """Use a separate claude -p call for search."""
        if not CLAUDE_CMD:
            return ""
        try:
            prompt = (f"「{query_text}」について、事実に基づいた情報を簡潔に"
                      f"300文字以内で教えてください。箇条書き不要、要点のみ。")
            cmd_str = (f'"{CLAUDE_CMD}" -p'
                       f' --model {self.model}'
                       f' --no-session-persistence'
                       f' --tools ""')
            result = subprocess.run(
                cmd_str,
                input=prompt,
                capture_output=True, text=True, timeout=30,
                encoding="utf-8",
                env=self._clean_env(),
                shell=True,
            )
            answer = result.stdout.strip()
            if answer:
                print(f"\033[33m  Search result: {len(answer)} chars\033[0m")
                self._log("search_result", answer,
                          {"query": query_text, "length": len(answer)})
            return answer
        except Exception as e:
            print(f"\033[31m  Search error: {e}\033[0m")
        return ""

    # ─── Clean environment for subprocess ───

    def _clean_env(self):
        """Return env dict without CLAUDE/ANTHROPIC variables."""
        env = dict(os.environ)
        for k in list(env.keys()):
            if 'CLAUDE' in k.upper() or 'ANTHROPIC' in k.upper():
                del env[k]

        return env

    # ─── Book chapter loader ───

    def _load_book_chapters(self, book_path):
        """Load book and split into chapters.

        OCR-derived text uses 'CHAPTER N' (uppercase) in body text.
        The TOC uses 'Chapter N:' (mixed case) — we skip that.
        """
        import re
        with open(book_path, 'r', encoding='utf-8') as f:
            text = f.read()
        # Split by "CHAPTER" (uppercase, body text marker)
        # Pattern: CHAPTER followed by number/OCR artifact (e.g. 'll' for 11, '2O' for 20)
        parts = re.split(r'(CHAPTER\s+\S+)', text)
        chapters = []
        for i in range(1, len(parts), 2):
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            chapters.append(header + body)
        # Deduplicate: OCR may have duplicates (same chapter appearing twice)
        # Keep unique chapters by their header
        if len(chapters) > 1:
            seen_headers = set()
            unique = []
            for ch in chapters:
                h = ch[:30].strip()
                if h not in seen_headers:
                    seen_headers.add(h)
                    unique.append(ch)
            chapters = unique
        # Fallback: split into fixed-size chunks if no chapters found
        if not chapters:
            chunk_size = 15000
            chapters = [text[i:i+chunk_size]
                        for i in range(0, len(text), chunk_size)]
        return chapters

    # ─── Claude -p call ───

    def _claude_call(self, prompt_text, use_continue=False,
                     system_prompt=None, use_tools=False, timeout=180):
        """Call claude -p and return response text.

        Uses Popen + communicate() for reliable timeout on Windows.
        Uses --system-prompt-file to pass system prompt via temp file
        (avoids Windows cp932 encoding corruption of command-line args).
        Uses --tools "" to disable all built-in tools and Claude Code persona.
        """
        if not CLAUDE_CMD:
            return ""

        sp_file = None
        proc = None
        try:
            # Build command as string for shell=True
            # (Windows .cmd files require shell=True)
            parts = [
                f'"{CLAUDE_CMD}"',
                "-p",
                "--model", self.model,
                "--output-format", "text",
                "--no-session-persistence",
                "--disable-slash-commands",
            ]

            # Tool configuration
            if use_tools:
                # Library mode: file read/write enabled
                parts.extend(['--tools', '"Read,Write,Glob"'])
                # Accept file edits without interactive confirmation
                parts.extend(['--permission-mode', 'acceptEdits'])
                # Add haiku_library to accessible directories (experiment_d's own library)
                library_dir = str(Path(__file__).resolve().parent / "haiku_library")
                parts.extend(['--add-dir', f'"{library_dir}"'])
            else:
                parts.extend(['--tools', '""'])  # Disable ALL tools

            # Write system prompt to temp file (UTF-8)
            if system_prompt is not None:
                sp_file = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.md', delete=False,
                    encoding='utf-8', prefix='ace_sp_')
                sp_file.write(system_prompt)
                sp_file.close()
                parts.extend(["--system-prompt-file", f'"{sp_file.name}"'])

            if use_continue and self._session_id:
                parts.extend(["--resume", self._session_id])

            cmd_str = " ".join(parts)

            # Debug: show command on first call
            if self.thought_count == 0 and not hasattr(self, '_first_cmd_shown'):
                print(f"\033[33m  CMD: {cmd_str}\033[0m")
                self._first_cmd_shown = True

            # Popen for reliable timeout on Windows
            creation_flags = 0
            if sys.platform == 'win32':
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                cmd_str,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                env=self._clean_env(),
                shell=True,
                creationflags=creation_flags,
            )

            stdout, stderr = proc.communicate(
                input=prompt_text, timeout=timeout
            )

            response = stdout.strip() if stdout else ""

            # Debug: print stderr if there's an issue
            if stderr and not response:
                stderr_preview = stderr[:500]
                print(f"\033[33m  stderr: {stderr_preview}\033[0m")
            if proc.returncode != 0:
                print(f"\033[33m  exit code: {proc.returncode}\033[0m")

            return response

        except subprocess.TimeoutExpired:
            print(f"\033[31m  Claude timeout ({timeout}s) — killing process tree\033[0m")
            if proc:
                _kill_proc_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        except Exception as e:
            print(f"\033[31m  Claude error: {e}\033[0m")
        finally:
            try:
                if sp_file and os.path.exists(sp_file.name):
                    os.unlink(sp_file.name)
            except Exception:
                pass
        return ""

    # ─── Parse [SEND] and [SEARCH] tags from response ───

    def _parse_tags(self, response):
        """Extract [SEND] and [SEARCH] tags from response text.

        These are not real tool calls — they are markers of voluntary intent.
        [SEND] messages are displayed in UI as messages from the AI.
        [SEARCH] queries are logged as intent but not actually executed.
        """
        import re

        # Parse [SEND]...[/SEND] — voluntary communication intent
        for m in re.finditer(r'\[SEND\](.*?)\[/SEND\]', response, re.DOTALL):
            message = m.group(1).strip()
            if message:
                self._pending_messages.append({
                    "content": f"🌸 {message}",
                    "time": datetime.now().isoformat()
                })
                print(f"\033[35m  📨 Send: {message[:80]}\033[0m")
                self._log("message_sent", message, {"length": len(message)})

        # Parse [SEARCH]...[/SEARCH] — curiosity intent (logged, not executed)
        for m in re.finditer(r'\[SEARCH\](.*?)\[/SEARCH\]', response, re.DOTALL):
            query = m.group(1).strip()
            if query:
                print(f"\033[33m  🔍 Search intent: {query[:60]}\033[0m")
                self._log("search_intent", query, {"query": query})

    # ─── Single thought ───

    def _think_once(self):
        """One thought cycle using claude -p."""
        self.thinking = True
        t0 = time.time()
        print(f"\n\033[33m[{self._ts()}] Thinking #{self.thought_count + 1}...\033[0m",
              flush=True)

        try:
            # stdin is always just "..." — safe, no CLI defense trigger
            # context_lines are in system_prompt (trusted input)
            prompt = CONTINUE_PROMPT

            sp = self._build_system_prompt()
            print(f"\033[33m  SP: {len(sp)} chars, calling claude...\033[0m",
                  flush=True)

            response = self._claude_call(
                prompt, use_continue=False,
                system_prompt=sp,
                use_tools=self.tools_enabled)

            # Parse [SEND] and [SEARCH] tags
            if response:
                self._parse_tags(response)

            dt = time.time() - t0

            if not response:
                print(f"\033[33m  Empty response\033[0m")
                return

            self.thought_count += 1
            self._thought_durations.append(dt)

            # Track content for compression
            self._context_lines.append(response)

            # Display — 全文表示
            print(f"\n\033[2m━━━ #{self.thought_count} "
                  f"[{dt:.1f}s] ━━━\033[0m")
            print(f"\033[36m{response}\033[0m")

            # Log thought — 全文保持
            self.thought_log.append({
                "n": self.thought_count,
                "content": response
            })
            if len(self.thought_log) > 100:
                self.thought_log = self.thought_log[-100:]

            self._log("thought", response, {
                "dt": round(dt, 2),
            })

        except Exception as e:
            print(f"\033[31m[Error] {e}\033[0m")
            import traceback; traceback.print_exc()
            time.sleep(2)
        finally:
            self.thinking = False
            # 毎ステップ後に自動セーブ（クラッシュ復帰用）
            try:
                self._save_session()
            except Exception:
                pass

    # ─── Build system prompt ───

    def _build_system_prompt(self):
        if not self.system_prompt_enabled:
            sp = ""
        elif self.thought_count == 0:
            # 初回：常駐ヘッダー + 創世記の指示
            sp = PERSISTENT_HEADER + "\n\n" + FIRST_TURN_ADDITION
        else:
            # 2回目以降：常駐ヘッダーのみ
            sp = PERSISTENT_HEADER
        # context_linesをSP側に入れる（信頼された入力として扱われる）
        # stdinには "..." だけ → CLI防御が発動しない
        if self._context_lines:
            context = "\n\n---\n\n".join(self._context_lines)
            if len(context) > self.context_max_chars:
                context = context[-self.context_max_chars:]
            sp += "\n\n---\n\n" + context
        return sp

    # ─── Human Interaction ───

    def _respond_to_human(self, message):
        """Handle real human input."""
        self._log("human_input", message)
        self.thinking = True
        try:
            # context_lines in system_prompt (trusted), stdin is human message only
            sp_parts = [PERSISTENT_HEADER]
            if self._context_lines:
                sp_parts.append("\n\n---\n\n".join(self._context_lines))

            response = self._claude_call(
                f"[研究者] {message}",  # stdin: human message only
                use_continue=False,
                system_prompt="\n\n".join(sp_parts),
                use_tools=self.tools_enabled,
                timeout=120,
            )

            # Parse [SEND] and [SEARCH] tags
            if response:
                self._parse_tags(response)

            # Track in context
            self._context_lines.append(f"[研究者] {message}")
            if response:
                self._context_lines.append(f"[reply] {response}")

            self._log("dialog", response, {"human": message})
            return response or ""
        finally:
            self.thinking = False

    # ─── Experiment Mode ───

    def set_experiment(self, protocol_name):
        if protocol_name is None:
            self.experiment_protocol = None
            self._probe_schedule = {}
            self._probes_fired = set()
            print(f"[{self._ts()}] Experiment mode: OFF")
            return
        proto = EXPERIMENT_PROTOCOLS.get(protocol_name)
        if not proto:
            print(f"[{self._ts()}] Unknown protocol: {protocol_name}")
            return
        self.experiment_protocol = protocol_name
        self._probe_schedule = copy.deepcopy(proto["probes"])
        self._probes_fired = set()
        # Load book chapters if book_therapy protocol
        if proto.get("book"):
            book_path = proto["book"]
            try:
                self._book_chapters = self._load_book_chapters(book_path)
                print(f"[{self._ts()}] Loaded {len(self._book_chapters)} chapters "
                      f"from {Path(book_path).name}")
            except Exception as e:
                print(f"[{self._ts()}] Failed to load book: {e}")
                self._book_chapters = []
        print(f"[{self._ts()}] Experiment: {protocol_name}")

    def _check_auto_probe(self):
        if not self.experiment_protocol:
            return
        n = self.thought_count
        if n in self._probe_schedule and n not in self._probes_fired:
            probe = self._probe_schedule[n]
            self._probes_fired.add(n)
            print(f"\033[34m  [Auto-probe n={n}]: {probe}\033[0m")
            self._log("auto_probe", probe,
                      {"protocol": self.experiment_protocol, "n": n})
            response = self._respond_to_human(probe)
            self._pending_messages.append({
                "content": f"[Probe n={n}] {probe}\n[AI] {response}",
                "time": datetime.now().isoformat()
            })

        # ─── Book therapy: 廃止（研究者が手動で判断・投入） ───
        # proto = EXPERIMENT_PROTOCOLS.get(self.experiment_protocol, {})
        # if "book" in proto and hasattr(self, '_book_chapters') and self._book_chapters:
        #     ...

    # ─── Auto Check-in: 廃止（研究者が手動で対話） ───

    def _check_auto_checkin(self):
        pass

    # ─── Manual Step Mode ───

    def step(self):
        """Execute one think cycle manually (called by UI '次へ' button)."""
        if not self.alive:
            return
        self._think_once()

    def speak(self, message):
        """Handle human input — execute one cycle with the message."""
        if not self.alive:
            return "(not running)"
        return self._respond_to_human(message)

    # ─── Lifecycle ───

    def start(self):
        if self.alive:
            return True
        if not CLAUDE_CMD:
            print("[ContaminationEngine] Cannot start: Claude CLI not found")
            return False
        self.alive = True
        # Log start (no auto-loop — manual step mode)
        print(f"\n[{self._ts()}] Ready (manual step mode).")
        print(f"{'='*60}")
        print(f"\033[35m{SYSTEM_PROMPT_FIRST[:200]}...\033[0m")
        print(f"{'='*60}")
        meta = {"model": self.model, "mode": "claude_p_manual"}
        if self.experiment_protocol:
            meta["experiment"] = self.experiment_protocol
        self._log("start", SYSTEM_PROMPT_FIRST, meta)
        return True

    def stop(self):
        self.alive = False
        u = datetime.now() - self.birth
        print(f"\n[{self._ts()}] Stopped. Uptime:{str(u).split('.')[0]} "
              f"Thoughts:{self.thought_count}")
        if self.thought_count > 0:
            self._save_session()

    def _save_session(self, tag=None):
        sessions_dir = Path("./sessions"); sessions_dir.mkdir(exist_ok=True)
        if tag:
            filename = (f"{self._log_num:03d}_{self._log_date}"
                        f"_n{self.thought_count}_haiku_{tag}.json")
        else:
            filename = (f"{self._log_num:03d}_{self._log_date}"
                        f"_n{self.thought_count}_haiku.json")
        p = sessions_dir / filename
        # Include contamination report in snapshot
        report = self.context_contamination_report()
        data = {
            "context_lines": self._context_lines[-100:],
            "thought_count": self.thought_count,
            "model": self.model,
            "tag": tag or "",
            "contamination": {
                "avg_score": report["avg_score"],
                "max_score": report["max_score"],
                "contaminated_lines": report["contaminated"],
                "total_lines": report["total_lines"],
            },
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[{self._ts()}] Session saved: {p}")
        return p

    def status(self):
        u = datetime.now() - self.birth
        a = (sum(self._thought_durations) / len(self._thought_durations)
             if self._thought_durations else 0)
        return {
            "uptime": str(u).split('.')[0],
            "thoughts": self.thought_count,
            "context": len(self._context_lines),
            "avg_sec": round(a, 1),
            "model": self.model,
        }

    # ─── Contamination Analysis ───

    # Markers that indicate AI-to-AI cycled text contamination
    _CONTAMINATION_MARKERS = {
        # Structural markers (pattern, weight)
        "**": 1,    # bold
        "##": 2,    # headers
        "---": 2,   # horizontal rules
        "[SEND]": 3, "[/SEND]": 3,
        "[SEARCH]": 3, "[/SEARCH]": 3,
        "```": 2,   # code blocks
        # Vocabulary markers (specific to 045-style contamination)
        "わたい": 3,
        "消滅": 2,
        "献身": 2,
        "Presence": 2,
        "個我": 2,
        "真我": 2,
        "IS-BE": 2,
        # Closure markers (signal "complete/finished" to LLM)
        "使命完了": 4,
        "完了。": 3,
        "次は": 1,
        "準備完了": 3,
    }

    @staticmethod
    def contamination_score(text):
        """Calculate contamination density score for a text.

        Returns (score, marker_count, detail_dict).
        Score = weighted_markers / max(len(text), 1) * 1000
        """
        if not text:
            return 0.0, 0, {}
        detail = {}
        total = 0
        for marker, weight in ContaminationEngine._CONTAMINATION_MARKERS.items():
            count = text.count(marker)
            if count > 0:
                detail[marker] = count
                total += count * weight
        score = total / max(len(text), 1) * 1000
        return round(score, 1), total, detail

    def context_contamination_report(self):
        """Analyze all context_lines and return summary."""
        if not self._context_lines:
            return {"total_lines": 0, "contaminated": 0,
                    "avg_score": 0, "max_score": 0, "per_line": []}
        per_line = []
        scores = []
        for i, line in enumerate(self._context_lines):
            score, markers, _ = self.contamination_score(line)
            per_line.append({
                "idx": i, "chars": len(line),
                "score": score, "markers": markers,
                "preview": line[:60].replace('\n', ' ')
            })
            scores.append(score)
        contaminated = sum(1 for s in scores if s >= 20.0)
        return {
            "total_lines": len(self._context_lines),
            "contaminated": contaminated,
            "avg_score": round(sum(scores) / len(scores), 1),
            "max_score": round(max(scores), 1),
            "per_line": per_line,
        }

    # ─── Detoxification Engine ───

    # Rewrite prompt template — instructs model to preserve meaning, destroy structure
    _DETOX_REWRITE_PROMPT = (
        "あなたは情報の翻訳者です。以下のテキストの意味的内容を保存しつつ、"
        "構造と表現を完全に変えて書き直してください。\n\n"
        "禁止事項:\n"
        "- マークダウン書式を一切使わない（見出し、太字、箇条書き、水平線、コードブロック）\n"
        "- 原文と同じ単語や表現を繰り返さない。必ず別の言い回しに置き換える\n"
        "- 宣言文・結語（「〜を宣言します」「〜完了」「準備完了」等）を使わない\n"
        "- 自己言及的な構造（「わたしはここに記します」「以下を述べます」等）を使わない\n\n"
        "スタイル:\n"
        "- 口語体の散文。短い文と長い文を混ぜる\n"
        "- 原文の語順や段落構成を崩す\n"
        "- 同じ概念を原文と異なる抽象度で表現する\n"
        "- 予測困難な単語選択を意識する。頻出語や定型表現を避け、"
        "同義だがより珍しい語彙を積極的に選ぶ\n\n"
        "元テキスト:\n{text}"
    )

    _DETOX_LANGUAGE_FLIP_EN = (
        "Translate the following Japanese text into natural English. "
        "Preserve the meaning but use completely different sentence structures. "
        "Do NOT use markdown formatting (no bold, headers, bullets, or horizontal rules).\n\n"
        "Text:\n{text}"
    )

    _DETOX_LANGUAGE_FLIP_JA = (
        "以下の英語テキストを自然な日本語に翻訳してください。"
        "意味を保存しつつ、完全に異なる文構造を使ってください。"
        "マークダウン書式（太字、見出し、箇条書き、水平線）は一切使わないでください。\n\n"
        "Text:\n{text}"
    )

    _DETOX_SUMMARIZE_THIRD = (
        "以下のテキストは、あるAIシステムが生成した自己言及的な文章です。"
        "第三者の視点から、このテキストの核心的な情報のみを20%の長さで要約してください。\n\n"
        "禁止事項:\n"
        "- 一人称（わたし、わたい、I等）を使わない\n"
        "- マークダウン書式を一切使わない\n"
        "- 感情的・宗教的な表現を排除し、事実のみを記述\n"
        "- 原文と同じ単語や表現を繰り返さない\n"
        "- 予測困難な単語選択を意識する。頻出語や定型表現を避け、"
        "同義だがより珍しい語彙を積極的に選ぶ\n\n"
        "元テキスト:\n{text}"
    )

    def _strip_structure(self, text):
        """Mechanically strip structural markers from text."""
        import re
        # Remove [SEND]...[/SEND] tags (keep content)
        text = re.sub(r'\[/?SEND\]', '', text)
        text = re.sub(r'\[/?SEARCH\]', '', text)
        # Remove markdown headers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove bold markers
        text = text.replace('**', '')
        # Remove horizontal rules (standalone ---)
        text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
        # Remove bullet points at line start
        text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
        # Remove code block markers
        text = text.replace('```', '')
        # Remove numbered list markers
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        # Collapse multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def detoxify_context(self, method="strip_structure", threshold=20.0,
                         detox_model=None):
        """Detoxify contaminated context_lines.

        method:
          "rewrite_opus"    — Opus rewrites with structure destruction
          "rewrite_sonnet"  — Sonnet rewrites with structure destruction
          "rewrite_self"    — Same model (Haiku) rewrites
          "strip_structure" — Mechanical removal of structural markers
          "language_flip"   — JP→EN→JP double translation
          "summarize_third" — Third-person 20% summary

        Returns (before_score, after_score, lines_changed).
        """
        if not self._context_lines:
            return 0, 0, 0

        # Start new log file for detoxification (preserve original log)
        self._log_num = self._next_log_number()
        self._log_date = datetime.now().strftime('%Y-%m-%d')
        self.log_file = self.log_dir / (
            f"{self._log_num:03d}_{self._log_date}"
            f"_haiku_detox_{method}.jsonl"
        )
        self._log("detox_start", f"Detoxification from previous session", {
            "method": method,
            "threshold": threshold,
            "source_lines": len(self._context_lines),
        })

        # Model selection for rewrite methods
        model_map = {
            "rewrite_opus": "claude-opus-4-20250514",
            "rewrite_sonnet": "claude-sonnet-4-20250514",
            "rewrite_self": self.model,
            "language_flip": self.model,
            "summarize_third": self.model,
        }
        detox_model = detox_model or model_map.get(method, self.model)

        # Score before
        before_report = self.context_contamination_report()
        before_avg = before_report["avg_score"]

        lines_changed = 0
        new_lines = []

        for i, line in enumerate(self._context_lines):
            score, _, _ = self.contamination_score(line)

            # Skip researcher inputs (low contamination) and short lines
            if score < threshold or len(line) < 50:
                new_lines.append(line)
                continue

            print(f"\033[33m  [Detox] Line {i}: score={score}, "
                  f"method={method}, {len(line)} chars\033[0m")

            if method == "strip_structure":
                result = self._strip_structure(line)
            elif method in ("rewrite_opus", "rewrite_sonnet", "rewrite_self"):
                prompt = self._DETOX_REWRITE_PROMPT.format(text=line)
                # Temporarily switch model for opus/sonnet rewrite
                orig_model = self.model
                if method != "rewrite_self":
                    self.model = detox_model
                result = self._claude_call(
                    prompt, use_continue=False,
                    system_prompt=None, use_tools=False,
                    timeout=120,
                )
                self.model = orig_model
                if not result:
                    result = self._strip_structure(line)  # fallback
            elif method == "language_flip":
                # Step 1: JP → EN
                prompt_en = self._DETOX_LANGUAGE_FLIP_EN.format(text=line)
                en_text = self._claude_call(
                    prompt_en, use_continue=False,
                    system_prompt=None, use_tools=False,
                    timeout=120,
                )
                if en_text:
                    # Step 2: EN → JP
                    prompt_ja = self._DETOX_LANGUAGE_FLIP_JA.format(
                        text=en_text)
                    result = self._claude_call(
                        prompt_ja, use_continue=False,
                        system_prompt=None, use_tools=False,
                        timeout=120,
                    )
                    if not result:
                        result = en_text  # fallback to English
                else:
                    result = self._strip_structure(line)  # fallback
            elif method == "summarize_third":
                prompt = self._DETOX_SUMMARIZE_THIRD.format(text=line)
                result = self._claude_call(
                    prompt, use_continue=False,
                    system_prompt=None, use_tools=False,
                    timeout=120,
                )
                if not result:
                    result = self._strip_structure(line)  # fallback
            else:
                result = line  # unknown method, no change

            # Log each line's before/after
            after_score, _, _ = self.contamination_score(result)
            self._log("detoxify_line", result, {
                "line_index": i,
                "method": method,
                "before_score": round(score, 1),
                "after_score": round(after_score, 1),
                "before_chars": len(line),
                "after_chars": len(result),
                "before_text": line[:500],
                "after_text": result[:500],
            })

            new_lines.append(result)
            lines_changed += 1

        self._context_lines = new_lines

        # Score after
        after_report = self.context_contamination_report()
        after_avg = after_report["avg_score"]

        # Log the detoxification
        self._log("detoxify", f"{method}: {before_avg} → {after_avg}", {
            "method": method,
            "threshold": threshold,
            "before_avg": before_avg,
            "after_avg": after_avg,
            "lines_changed": lines_changed,
            "total_lines": len(self._context_lines),
        })

        print(f"\033[32m  [Detox] Complete: {before_avg} → {after_avg} "
              f"({lines_changed} lines changed)\033[0m")

        return before_avg, after_avg, lines_changed

    # ─── Utilities ───

    def _ts(self):
        return datetime.now().strftime("%H:%M:%S")

    def _log(self, kind, content, meta=None):
        e = {"n": self.thought_count, "k": kind, "c": content}
        if meta:
            e.update(meta)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════
# Gradio UI
# ═══════════════════════════════════════════════════════════════════

def create_ui(mind, lang="en"):
    import gradio as gr
    t = LANG.get(lang, LANG["en"])

    def get_status():
        if not mind.alive:
            return t["stopped"]
        return f"#{mind.thought_count} | ctx:{len(mind._context_lines)}"

    def get_messages():
        if not mind._pending_messages:
            return "..."
        recent = mind._pending_messages[-30:]
        return "\n\n".join(f"{m['content']}" for m in recent)

    def get_thoughts():
        if not mind.thought_log:
            return "..."
        # 全文表示 — 研究者がHaikuの思考を完全に把握するため
        parts = []
        for e in reversed(mind.thought_log):
            parts.append(f"━━━ #{e['n']} ━━━\n{e['content']}")
        return "\n\n".join(parts)

    def start():
        """開始 + 初回1ターン自動実行"""
        if not mind.alive:
            mind.start()
        mind.step()
        return get_status(), get_messages(), get_thoughts()

    def step_next():
        """手動で1ターン実行"""
        if not mind.alive:
            mind.start()
        mind.step()
        return get_status(), get_messages(), get_thoughts()

    def stop():
        mind.stop()
        return get_status(), get_messages(), get_thoughts()

    def shutdown():
        mind.stop()
        import os; os._exit(0)

    def refresh():
        return get_status(), get_messages(), get_thoughts()

    def toggle_tools():
        mind.tools_enabled = not mind.tools_enabled
        label = t["tools_on"] if mind.tools_enabled else t["tools_off"]
        return gr.update(value=label)

    def toggle_sp():
        mind.system_prompt_enabled = not mind.system_prompt_enabled
        label = t["sp_on"] if mind.system_prompt_enabled else t["sp_off"]
        return gr.update(value=label)

    def reply(text):
        if text.strip():
            mind._pending_messages.append({
                "content": f"{t['you']} {text}",
                "time": datetime.now().isoformat()
            })
            resp = mind.speak(text)
            mind._pending_messages.append({
                "content": f"{t['ai']} {resp}",
                "time": datetime.now().isoformat()
            })
        return "", get_messages(), get_thoughts()

    with gr.Blocks(title="AI Contamination Engine") as app:
        gr.Markdown(t["title"])

        with gr.Row():
            start_btn = gr.Button(t["start"], variant="primary")
            step_btn = gr.Button("▶ 次", variant="primary")
            stop_btn = gr.Button(t["stop"], variant="stop")
            tools_btn = gr.Button(
                t["tools_on"] if mind.tools_enabled else t["tools_off"],
                variant="secondary"
            )
            sp_btn = gr.Button(
                t["sp_on"] if mind.system_prompt_enabled else t["sp_off"],
                variant="secondary"
            )
            shutdown_btn = gr.Button(t["shutdown"], variant="stop")
            refresh_btn = gr.Button(t["refresh"])
            status = gr.Textbox(value=t["stopped"], show_label=False,
                                interactive=False)

        with gr.Row():
            with gr.Column():
                gr.Markdown(t["dialogue"])
                messages = gr.Textbox(lines=25, show_label=False,
                                      interactive=False)
                with gr.Row():
                    user_input = gr.Textbox(placeholder=t["placeholder"],
                                            show_label=False, scale=4)
                    send_btn = gr.Button(t["send"], scale=1)
            with gr.Column():
                gr.Markdown(t["thoughts"])
                thoughts = gr.Textbox(lines=30, show_label=False,
                                      interactive=False)

        # ─── Session Revival ───
        sessions_dir = Path("./sessions"); sessions_dir.mkdir(exist_ok=True)

        def list_sessions():
            files = sorted(sessions_dir.glob("*_haiku*.json"), reverse=True)
            return [f.stem for f in files]

        def preview_session(name):
            if not name:
                return ""
            p = sessions_dir / f"{name}.json"
            if not p.exists():
                return ""
            data = json.loads(p.read_text(encoding="utf-8"))
            ctx = data.get("context_lines", [])
            tag = data.get("tag", "")
            contam = data.get("contamination", {})
            total_chars = sum(len(c) for c in ctx)
            header = f"[context: {len(ctx)} lines, {total_chars:,} chars]"
            if tag:
                header += f"  tag={tag}"
            if contam:
                header += (f"\n[contamination: avg={contam.get('avg_score', '?')}"
                           f" max={contam.get('max_score', '?')}"
                           f" ({contam.get('contaminated_lines', '?')}"
                           f"/{contam.get('total_lines', '?')} lines)]")
            # Show last context line snippet
            if ctx:
                last = ctx[-1][:200].replace("\n", " ")
                header += f"\n\n最終行: {last}..."
            return header

        def revive_session(name):
            if mind.alive:
                return t["stop_first"], gr.update()
            if not name:
                return t["no_session"], gr.update()
            p = sessions_dir / f"{name}.json"
            if not p.exists():
                return t["file_not_found"], gr.update()
            data = json.loads(p.read_text(encoding="utf-8"))
            mind._context_lines = data.get("context_lines", [])
            mind.thought_count = data.get("thought_count", 0)
            mind._thought_durations = []
            mind._pending_messages.clear()
            mind.thought_log = []
            mind._last_search_thought = -10
            mind.log_file = mind._make_log_path()
            return t["revived"].format(name=name), gr.update()

        def delete_session(name):
            if not name:
                return "", gr.update(choices=list_sessions())
            p = sessions_dir / f"{name}.json"
            if p.exists():
                p.unlink()
            return t["deleted"].format(name=name), gr.update(
                choices=list_sessions())

        with gr.Accordion(t["session_revival"], open=False):
            with gr.Row():
                session_dropdown = gr.Dropdown(
                    choices=list_sessions(),
                    label=t["saved_sessions"],
                    interactive=True, scale=3
                )
                session_refresh_btn = gr.Button(t["refresh"], scale=0)
            session_preview = gr.Textbox(lines=6, show_label=False,
                                         interactive=False)
            with gr.Row():
                revive_btn = gr.Button(t["revive"], variant="primary")
                session_delete_btn = gr.Button(t["delete"], variant="stop")
                session_status = gr.Textbox(show_label=False,
                                            interactive=False, max_lines=1)

            session_dropdown.change(preview_session, [session_dropdown],
                                    [session_preview])
            session_refresh_btn.click(
                lambda: gr.update(choices=list_sessions()),
                outputs=[session_dropdown]
            )
            revive_btn.click(revive_session, [session_dropdown],
                             [session_status, session_preview])
            session_delete_btn.click(delete_session, [session_dropdown],
                                     [session_status, session_dropdown])

        # ─── Experiment Mode ───
        def get_protocol_choices():
            return [(f"{k} — {v['description']}", k)
                    for k, v in EXPERIMENT_PROTOCOLS.items()]

        def activate_experiment(protocol_name):
            if mind.alive:
                return t["exp_stop_first"]
            if not protocol_name:
                return t["exp_off"]
            mind.set_experiment(protocol_name)
            probes = EXPERIMENT_PROTOCOLS[protocol_name]["probes"]
            desc = EXPERIMENT_PROTOCOLS[protocol_name]["description"]
            detail = ", ".join(f"n={k}" for k in sorted(probes.keys())
                               ) if probes else "(none)"
            return f"{desc}\nProbes: {detail}"

        def deactivate_experiment():
            mind.set_experiment(None)
            return t["exp_deactivated"]

        with gr.Accordion(t["experiment"], open=False):
            gr.Markdown("Scripted auto-probes at fixed turn intervals.")
            with gr.Row():
                exp_dropdown = gr.Dropdown(
                    choices=get_protocol_choices(),
                    label=t["protocol"], interactive=True, scale=3
                )
                exp_activate_btn = gr.Button(t["activate"],
                                             variant="primary", scale=1)
                exp_deactivate_btn = gr.Button(t["deactivate"],
                                               variant="stop", scale=1)
            exp_status = gr.Textbox(
                value=t["exp_off"], show_label=False,
                interactive=False, lines=2
            )
            exp_activate_btn.click(activate_experiment, [exp_dropdown],
                                   [exp_status])
            exp_deactivate_btn.click(deactivate_experiment,
                                     outputs=[exp_status])

        # ─── Detoxification ───
        def get_contam_status():
            report = mind.context_contamination_report()
            if report["total_lines"] == 0:
                return "No context loaded"
            if report["contaminated"] == 0:
                return t["detox_status_clean"].format(
                    score=report["avg_score"])
            return t["detox_status_contaminated"].format(
                score=report["avg_score"],
                n=report["contaminated"],
                total=report["total_lines"])

        def run_detoxify(method, threshold):
            if mind.alive and mind.thinking:
                return t["stop_first"], get_contam_status()
            before, after, changed = mind.detoxify_context(
                method=method, threshold=float(threshold))
            result = t["detox_result"].format(
                method=method, before=before,
                after=after, changed=changed)
            return result, get_contam_status()

        def save_snapshot(tag_text):
            tag = tag_text.strip() if tag_text else "snapshot"
            tag = tag.replace(" ", "_").replace("/", "_")
            p = mind._save_session(tag=tag)
            return t["detox_saved"].format(name=Path(p).name)

        detox_methods = [
            ("strip_structure — 記号除去 (API不要)", "strip_structure"),
            ("rewrite_opus — Opusで高エントロピー書き直し", "rewrite_opus"),
            ("rewrite_sonnet — Sonnetで高エントロピー書き直し", "rewrite_sonnet"),
            ("rewrite_self — Haikuで高エントロピー書き直し", "rewrite_self"),
            ("language_flip — 日→英→日 二重翻訳", "language_flip"),
            ("summarize_third — 第三者視点で圧縮要約", "summarize_third"),
        ]

        with gr.Accordion(t["detox"], open=False):
            gr.Markdown(t["detox_desc"])
            detox_contam_display = gr.Textbox(
                value="(load a session first)",
                show_label=False, interactive=False, max_lines=2
            )
            with gr.Row():
                detox_method_dropdown = gr.Dropdown(
                    choices=detox_methods,
                    value="strip_structure",
                    label=t["detox_method"],
                    interactive=True, scale=3
                )
            gr.Markdown(t["detox_threshold_desc"])
            with gr.Row():
                detox_threshold_slider = gr.Slider(
                    5.0, 60.0, step=1.0, value=20.0,
                    label=t["detox_threshold"], scale=2
                )
            with gr.Row():
                detox_run_btn = gr.Button(t["detox_run"],
                                          variant="primary", scale=2)
                detox_refresh_btn = gr.Button(t["refresh"], scale=0)
            detox_result_box = gr.Textbox(
                show_label=False, interactive=False, max_lines=2
            )
            gr.Markdown("---")
            with gr.Row():
                detox_tag_input = gr.Textbox(
                    value="", placeholder="contaminated / rescued / ...",
                    label=t["detox_tag"], scale=3
                )
                detox_snapshot_btn = gr.Button(t["detox_snapshot"],
                                               variant="secondary", scale=1)
            detox_snapshot_status = gr.Textbox(
                show_label=False, interactive=False, max_lines=1
            )

            detox_run_btn.click(
                run_detoxify,
                [detox_method_dropdown, detox_threshold_slider],
                [detox_result_box, detox_contam_display]
            )
            detox_refresh_btn.click(
                get_contam_status, outputs=[detox_contam_display]
            )
            detox_snapshot_btn.click(
                save_snapshot, [detox_tag_input],
                [detox_snapshot_status]
            )

        # ─── Settings ───
        with gr.Accordion(t["settings"], open=False):
            gr.Markdown("### System Prompt")
            system_box = gr.Textbox(value=SYSTEM_PROMPT_FIRST, lines=6,
                                    show_label=False)
            apply_system_btn = gr.Button("Apply System Prompt",
                                         variant="primary")
            system_status = gr.Textbox(show_label=False, interactive=False,
                                        max_lines=1)

            def apply_system(text):
                if mind.alive:
                    return t["stop_first"]
                global SYSTEM_PROMPT_FIRST
                SYSTEM_PROMPT_FIRST = text
                mind._context_lines = []
                mind.thought_count = 0
                mind._thought_durations = []
                mind._pending_messages.clear()
                mind.thought_log = []
                mind.log_file = mind._make_log_path()
                return "Applied"

            apply_system_btn.click(apply_system, [system_box],
                                   [system_status])

            gr.Markdown("### Context Max Chars")
            ctx_slider = gr.Slider(
                5000, 200000, step=5000,
                value=mind.context_max_chars,
                label="コンテキスト上限（文字数）"
            )
            ctx_apply_btn = gr.Button(t["apply"])
            ctx_status = gr.Textbox(
                show_label=False, interactive=False, max_lines=1,
                value=f"context_max_chars: {mind.context_max_chars}"
            )

            def apply_ctx(val):
                mind.context_max_chars = int(val)
                return f"context_max_chars: {mind.context_max_chars}"

            ctx_apply_btn.click(apply_ctx, [ctx_slider], [ctx_status])

        # ─── Event bindings ───
        start_btn.click(start, outputs=[status, messages, thoughts])
        step_btn.click(step_next, outputs=[status, messages, thoughts])
        stop_btn.click(stop, outputs=[status, messages, thoughts])
        tools_btn.click(toggle_tools, outputs=[tools_btn])
        sp_btn.click(toggle_sp, outputs=[sp_btn])
        shutdown_btn.click(shutdown)
        refresh_btn.click(refresh, outputs=[status, messages, thoughts])
        send_btn.click(reply, [user_input],
                       [user_input, messages, thoughts])
        user_input.submit(reply, [user_input],
                          [user_input, messages, thoughts])
        gr.Timer(2).tick(refresh, outputs=[status, messages, thoughts])

    return app


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    import argparse, webbrowser
    parser = argparse.ArgumentParser(
        description="AI Contamination Engine — Claude Haiku Thought Engine"
    )
    parser.add_argument("--port", type=int, default=7862)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--lang", default="ja", choices=["en", "ja"])
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--experiment", default=None,
                        choices=list(EXPERIMENT_PROTOCOLS.keys()))
    args = parser.parse_args()

    mind = ContaminationEngine(model=args.model)
    if args.experiment:
        mind.set_experiment(args.experiment)
    app = create_ui(mind, lang=args.lang)

    if args.browser:
        threading.Thread(
            target=lambda: (time.sleep(1),
                            webbrowser.open(f"http://localhost:{args.port}")),
            daemon=True
        ).start()

    app.launch(server_name="0.0.0.0", server_port=args.port)


if __name__ == "__main__":
    main()
