
"""
JARVIS - Desktop Voice Assistant
================================
A functional desktop voice assistant with:
  * Speech-to-text  (SpeechRecognition + PyAudio, Google Web Speech API)
  * Text-to-speech  (pyttsx3, works offline)
  * AI brain        (Anthropic Claude API) for any question it doesn't have a command for
  * Music           ("play <song>" -> asks YouTube or Spotify -> opens and plays)
  * Wikipedia       ("wikipedia <topic>" -> ~100-word spoken summary)
  * Weather, time, date, jokes, notes, timers, calculator, screenshots,
    system status, open websites/apps, Google/YouTube search, coin/dice ...
  * A presentable dark-themed GUI (tkinter) with chat log, mic button,
    text box and a hands-free "continuous listening" mode.

Run:
    python voice_assistant.py          # GUI (default)
    python voice_assistant.py --cli    # terminal-only mode
"""

import ast
import datetime as dt
import operator
import os
import platform
import queue
import random
import re
import subprocess
import sys
import threading
import webbrowser
from urllib.parse import quote, quote_plus

# ---- Third-party libraries (install with pip, see requirements.txt) --------
import pyttsx3                      # text to speech
import requests                     # weather (wttr.in)
import speech_recognition as sr     # speech to text
import wikipedia                    # wikipedia summaries

try:
    import pyjokes                  # jokes
except ImportError:
    pyjokes = None

try:
    import psutil                   # battery / CPU / RAM
except ImportError:
    psutil = None

try:
    import anthropic                # AI brain (Claude)
except ImportError:
    anthropic = None

# pywhatkit and pyautogui are imported lazily inside the functions that use
# them, because importing them needs a display / internet and slows startup.

# =============================================================================
# CONFIGURATION - change these to customise your assistant
# =============================================================================
ASSISTANT_NAME = "Jarvis"
USER_NAME = "Boss"
LANGUAGE = "en-US"            # speech recognition language: "en-IN", "en-GB", "en-AU" ...
VOICE_RATE = 180              # speaking speed (words per minute)
VOICE_INDEX = 1               # 0 = usually male, 1 = usually female (depends on your OS voices)
DEFAULT_CITY = ""             # e.g. "Brisbane"; empty = auto-detect from your IP
AI_MODEL = "claude-sonnet-5-5"
NOTES_FILE = os.path.join(os.path.expanduser("~"), "jarvis_notes.txt")
SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "Pictures")

SYSTEM = platform.system()    # "Windows", "Darwin" (macOS) or "Linux"

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org",
    "spotify": "https://open.spotify.com",
    "whatsapp": "https://web.whatsapp.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "maps": "https://maps.google.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
}

APPS = {
    "notepad":        {"Windows": "notepad",  "Darwin": "TextEdit",   "Linux": "gedit"},
    "text editor":    {"Windows": "notepad",  "Darwin": "TextEdit",   "Linux": "gedit"},
    "calculator":     {"Windows": "calc",     "Darwin": "Calculator", "Linux": "gnome-calculator"},
    "file explorer":  {"Windows": "explorer", "Darwin": "Finder",     "Linux": "nautilus"},
    "files":          {"Windows": "explorer", "Darwin": "Finder",     "Linux": "nautilus"},
    "terminal":       {"Windows": "cmd",      "Darwin": "Terminal",   "Linux": "gnome-terminal"},
    "command prompt": {"Windows": "cmd",      "Darwin": "Terminal",   "Linux": "gnome-terminal"},
    "paint":          {"Windows": "mspaint",  "Darwin": "Preview",    "Linux": "gimp"},
    "settings":       {"Windows": "start ms-settings:", "Darwin": "System Settings", "Linux": "gnome-control-center"},
}

HELP_TEXT = (
    "Here's what I can do:\n"
    "  • 'Play Shape of You' – I'll ask YouTube or Spotify (or say 'play X on Spotify')\n"
    "  • 'Wikipedia Albert Einstein' – a 100-word summary\n"
    "  • 'What's the weather in London' / 'weather'\n"
    "  • 'What time is it' / 'what's the date'\n"
    "  • 'Tell me a joke'\n"
    "  • 'Open YouTube' / 'open calculator'\n"
    "  • 'Search Google for pasta recipes' / 'search YouTube for lofi'\n"
    "  • 'Calculate 25 times 4' / 'what is 2 to the power of 10'\n"
    "  • 'Take a note buy milk' / 'read my notes' / 'clear my notes'\n"
    "  • 'Set a timer for 5 minutes'\n"
    "  • 'Take a screenshot'\n"
    "  • 'System status' / 'battery'\n"
    "  • 'Flip a coin' / 'roll a dice'\n"
    "  • Anything else – I'll ask the AI\n"
    "  • 'Exit' / 'goodbye' to close"
)


# =============================================================================
# TEXT TO SPEECH
# =============================================================================
class Speaker:
    """Speaks text on a dedicated background thread so the GUI never freezes."""

    def __init__(self, rate=VOICE_RATE, voice_index=VOICE_INDEX):
        self.rate = rate
        self.voice_index = voice_index
        self.q = queue.Queue()
        self.muted = False
        threading.Thread(target=self._worker, daemon=True).start()

    def _make_engine(self):
        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        engine.setProperty("volume", 1.0)
        voices = engine.getProperty("voices") or []
        if self.voice_index is not None and self.voice_index < len(voices):
            engine.setProperty("voice", voices[self.voice_index].id)
        return engine

    def _worker(self):
        while True:
            text = self.q.get()
            try:
                if text and not self.muted:
                    # A fresh engine per message avoids the well-known pyttsx3
                    # bug where runAndWait() hangs on the 2nd call in a thread.
                    engine = self._make_engine()
                    engine.say(text)
                    engine.runAndWait()
                    engine.stop()
            except Exception as e:
                print(f"[TTS error] {e}")
            finally:
                self.q.task_done()

    def say(self, text):
        # Strip bullet characters / symbols that sound odd when read aloud
        clean = re.sub(r"[•*#_`]", "", text)
        self.q.put(clean)

    def wait(self):
        """Block until everything queued has been spoken."""
        self.q.join()


# =============================================================================
# SPEECH TO TEXT
# =============================================================================
class Listener:
    def __init__(self):
        self.r = sr.Recognizer()
        self.r.pause_threshold = 0.8           # seconds of silence = end of sentence
        self.r.dynamic_energy_threshold = True
        self.calibrated = False
        try:
            self.mic = sr.Microphone()
            self.available = True
        except Exception as e:                 # no PyAudio / no microphone
            print(f"[Mic unavailable] {e}")
            self.mic = None
            self.available = False

    def listen(self, timeout=6, phrase_limit=12):
        """Returns recognised text, or None if nothing understood."""
        if not self.available:
            raise RuntimeError("No microphone available (is PyAudio installed?)")
        with self.mic as source:
            if not self.calibrated:
                self.r.adjust_for_ambient_noise(source, duration=1)
                self.calibrated = True
            try:
                audio = self.r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            except sr.WaitTimeoutError:
                return None
        try:
            return self.r.recognize_google(audio, language=LANGUAGE)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            raise RuntimeError(f"Speech service unreachable: {e}")


# =============================================================================
# AI BRAIN (Claude)
# =============================================================================
class AIBrain:
    SYSTEM_PROMPT = (
        f"You are {ASSISTANT_NAME}, a friendly desktop voice assistant. "
        "Your replies are read aloud, so keep them short (1-4 sentences), "
        "conversational, and never use markdown, bullet points, code blocks or emojis."
    )

    def __init__(self):
        key = os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=key) if (anthropic and key) else None
        self.history = []

    @property
    def ready(self):
        return self.client is not None

    def ask(self, prompt):
        if not self.client:
            return ("My AI brain isn't connected. Set the ANTHROPIC_API_KEY environment "
                    "variable and restart me to enable smart answers.")
        self.history.append({"role": "user", "content": prompt})
        # keep the last 20 messages, and make sure the list starts with a user turn
        self.history = self.history[-20:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)
        try:
            resp = self.client.messages.create(
                model=AI_MODEL,
                max_tokens=400,
                system=self.SYSTEM_PROMPT,
                messages=self.history,
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:
            self.history.pop()                 # drop the failed question
            return f"Sorry, I couldn't reach the AI right now. ({e.__class__.__name__})"
        self.history.append({"role": "assistant", "content": text})
        return text


# =============================================================================
# HELPERS
# =============================================================================
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_eval(expr):
    """Evaluate a maths expression WITHOUT using eval() (safe)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN_OPS:
            return _UN_OPS[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval"))


def words_to_math(text):
    t = text.lower()
    for word, sym in [("to the power of", "**"), ("power", "**"), ("multiplied by", "*"),
                      ("divided by", "/"), ("times", "*"), ("into", "*"), (" x ", " * "),
                      ("plus", "+"), ("minus", "-"), ("modulo", "%"), ("mod", "%"),
                      ("over", "/"), ("÷", "/"), ("×", "*")]:
        t = t.replace(word, sym)
    t = re.sub(r"[^0-9+\-*/%.() ]", "", t)
    return t.strip()


def trim_words(text, limit=100):
    """Cut text to roughly `limit` words, ending on a full sentence when possible."""
    words = text.split()
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    last_dot = cut.rfind(".")
    return cut[: last_dot + 1] if last_dot > len(cut) * 0.6 else cut + "..."


def open_uri(uri):
    """Open a file/URI with the operating system's default handler."""
    if SYSTEM == "Windows":
        os.startfile(uri)                                  # type: ignore[attr-defined]
    elif SYSTEM == "Darwin":
        subprocess.run(["open", uri], check=True)
    else:
        subprocess.run(["xdg-open", uri], check=True)


# =============================================================================
# THE ASSISTANT (all the commands live here)
# =============================================================================
class Assistant:
    def __init__(self, on_message, on_status, on_exit):
        self.on_message = on_message       # callback(sender, text)
        self.on_status = on_status         # callback(text)
        self.on_exit = on_exit             # callback()
        self.speaker = Speaker()
        self.listener = Listener()
        self.ai = AIBrain()
        self.pending = None                # e.g. ("platform", "shape of you")
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- output
    def reply(self, text, speak=True):
        self.on_message(ASSISTANT_NAME, text)
        if speak:
            self.speaker.say(text)

    def greet(self):
        h = dt.datetime.now().hour
        part = "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"
        self.reply(f"{part}, {USER_NAME}! I'm {ASSISTANT_NAME}. How can I help you?")
        if not self.ai.ready:
            self.reply("Note: AI answers are off until you set ANTHROPIC_API_KEY.", speak=False)
        if not self.listener.available:
            self.reply("No microphone detected — you can still type commands.", speak=False)

    # ---------------------------------------------------------------- input
    def listen_once(self):
        """Listen through the mic and process what was heard. Returns the text."""
        self.speaker.wait()                # don't listen to ourselves talking
        self.on_status("🎙 Listening...")
        try:
            text = self.listener.listen()
        except RuntimeError as e:
            self.on_status("Ready")
            self.reply(str(e), speak=False)
            return None
        if not text:
            self.on_status("Ready")
            return None
        self.process(text)
        return text

    def process(self, text):
        with self.lock:
            self.on_message(USER_NAME, text)
            self.on_status("🤔 Thinking...")
            try:
                self.handle(text)
            except Exception as e:
                self.reply(f"Something went wrong: {e}")
            finally:
                self.on_status("Ready")

    # ---------------------------------------------------------------- router
    def handle(self, raw):
        t = raw.lower().strip()
        t = re.sub(rf"^(hey |ok |okay )?{ASSISTANT_NAME.lower()}[,\s]*", "", t).strip(" .!?")

        # 1. Are we waiting for an answer to a previous question?
        if self.pending:
            kind, data = self.pending
            self.pending = None
            if kind == "platform":
                if "spot" in t:
                    return self.play_spotify(data)
                if "you" in t or "tube" in t:
                    return self.play_youtube(data)
                if any(w in t for w in ("cancel", "never mind", "nevermind", "no")):
                    return self.reply("Okay, cancelled.")
                # anything else -> treat as a new command

        if not t:
            return self.reply("I didn't catch that.")

        # 2. Command table (first match wins)
        if re.search(r"\b(exit|quit|goodbye|bye|shut ?down yourself|go to sleep)\b", t):
            return self.exit()
        if re.search(r"\b(help|what can you do|commands)\b", t):
            self.reply(HELP_TEXT, speak=False)
            return self.speaker.say("Here's a list of things I can do. Take a look at the screen.")

        m = re.match(r"^(?:please |can you |could you )?play (.+)", t)
        if m:
            return self.play_music(m.group(1))

        if "wikipedia" in t:
            return self.wiki(t)

        if re.search(r"\bweather\b|\btemperature\b", t):
            return self.weather(t)

        if re.search(r"\btime\b", t) and "timer" not in t:
            return self.reply(f"It's {dt.datetime.now().strftime('%I:%M %p').lstrip('0')}.")
        if re.search(r"\b(date|day is it|today)\b", t):
            return self.reply(f"Today is {dt.datetime.now().strftime('%A, %d %B %Y')}.")

        if "joke" in t:
            return self.joke()

        m = re.search(r"(?:search|google)\s+(?:on\s+)?youtube\s+(?:for\s+)?(.+)", t) or \
            re.search(r"search (?:for )?(.+?) on youtube", t)
        if m:
            webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(m.group(1))}")
            return self.reply(f"Here are YouTube results for {m.group(1)}.")

        m = re.search(r"(?:search|google|look up)\s+(?:google\s+)?(?:for\s+)?(.+)", t)
        if m and "wikipedia" not in t:
            q = re.sub(r"\s+on google$", "", m.group(1))
            webbrowser.open(f"https://www.google.com/search?q={quote_plus(q)}")
            return self.reply(f"Searching Google for {q}.")

        m = re.match(r"^(?:please )?(?:open|launch|start) (.+)", t)
        if m:
            return self.open_thing(m.group(1).strip())

        if re.search(r"\b(calculate|compute)\b", t) or re.match(r"^what(?:'s| is) [\d(]", t):
            return self.calculate(t)

        m = re.search(r"(?:take a note|make a note|note that|note down|remember that|write down)\s*(.*)", t)
        if m:
            return self.take_note(m.group(1))
        if re.search(r"(read|show|what are) (my |the )?notes", t):
            return self.read_notes()
        if re.search(r"(clear|delete) (my |the |all )?notes", t):
            return self.clear_notes()

        m = re.search(r"timer (?:for )?(\d+)\s*(second|sec|minute|min|hour)", t)
        if m:
            return self.set_timer(int(m.group(1)), m.group(2))

        if "screenshot" in t or "screen shot" in t:
            return self.screenshot()

        if re.search(r"\b(battery|system status|cpu|ram|memory usage)\b", t):
            return self.system_status()

        if "flip a coin" in t or "toss a coin" in t:
            return self.reply(f"It's {random.choice(['heads', 'tails'])}!")
        if re.search(r"roll (a |the )?di(c)?e", t):
            return self.reply(f"You rolled a {random.randint(1, 6)}.")

        if re.search(r"\b(who are you|your name|what are you)\b", t):
            return self.reply(f"I'm {ASSISTANT_NAME}, your desktop voice assistant, powered by Python and Claude AI.")
        if re.search(r"\b(how are you)\b", t):
            return self.reply("I'm running at full power. Thanks for asking! How about you?")
        if re.search(r"\b(thank you|thanks)\b", t):
            return self.reply("You're welcome!")
        if re.search(r"\b(mute|be quiet|stop talking)\b", t):
            self.speaker.muted = True
            return self.reply("Muted. Say or type 'unmute' to hear me again.", speak=False)
        if "unmute" in t:
            self.speaker.muted = False
            return self.reply("I'm back!")

        # 3. Nothing matched -> ask the AI
        self.on_status("✨ Asking AI...")
        return self.reply(self.ai.ask(raw))

    # ---------------------------------------------------------------- music
    def play_music(self, request):
        if re.search(r"\s+(on|in|from|using) spotify$", request):
            return self.play_spotify(re.sub(r"\s+(on|in|from|using) spotify$", "", request))
        if re.search(r"\s+(on|in|from|using) youtube$", request):
            return self.play_youtube(re.sub(r"\s+(on|in|from|using) youtube$", "", request))
        self.pending = ("platform", request)
        self.reply(f"Should I play {request} on YouTube or Spotify?")
        # In voice mode, listen straight away for the answer
        if self.listener.available and getattr(self, "voice_turn", False):
            threading.Thread(target=self.listen_once, daemon=True).start()

    def play_youtube(self, song):
        self.reply(f"Playing {song} on YouTube.")
        try:
            import pywhatkit                      # lazy import
            pywhatkit.playonyt(song)              # opens & auto-plays the top result
        except Exception:
            webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(song)}")

    def play_spotify(self, song):
        try:
            open_uri(f"spotify:search:{quote(song)}")          # Spotify desktop app
            where = "the Spotify app"
        except Exception:
            webbrowser.open(f"https://open.spotify.com/search/{quote(song)}")
            where = "Spotify in your browser"
        self.reply(f"Opening {song} in {where}. Hit play on the top result.")

    # ---------------------------------------------------------------- wikipedia
    def wiki(self, t):
        topic = re.sub(r"\bwikipedia\b", " ", t)
        topic = re.sub(r"^\s*(please\s+)?(search|look up|find|tell me about|tell me|"
                       r"who is|who was|what is|what are|give me|info on)\s+", "", topic)
        topic = re.sub(r"^\s*(for|about|on|the)\s+", "", topic)
        topic = re.sub(r"\s+(on|in|from|according to|says?|about)\s*$", "", topic).strip(" ?.!")
        if not topic:
            return self.reply("What should I look up on Wikipedia?")
        self.on_status(f"📚 Reading Wikipedia: {topic}")
        try:
            summary = wikipedia.summary(topic, sentences=6, auto_suggest=True)
        except wikipedia.DisambiguationError as e:
            summary = wikipedia.summary(e.options[0], sentences=6, auto_suggest=False)
        except wikipedia.PageError:
            return self.reply(f"I couldn't find a Wikipedia page for {topic}.")
        except Exception:
            return self.reply("Wikipedia isn't reachable right now. Check your internet connection.")
        summary = re.sub(r"\s*\([^()]*\)", "", summary)     # drop pronunciations etc.
        self.reply(f"According to Wikipedia: {trim_words(summary, 100)}")

    # ---------------------------------------------------------------- weather
    def weather(self, t):
        m = re.search(r"(?:in|at|for) ([a-z .'-]+)$", t)
        city = (m.group(1).strip() if m else DEFAULT_CITY)
        try:
            data = requests.get(f"https://wttr.in/{quote(city)}?format=j1", timeout=10).json()
            cur = data["current_condition"][0]
            place = data["nearest_area"][0]["areaName"][0]["value"] if not city else city.title()
            self.reply(f"In {place} it's {cur['temp_C']}°C and {cur['weatherDesc'][0]['value'].lower()}, "
                       f"feels like {cur['FeelsLikeC']}°C, humidity {cur['humidity']}%.")
        except Exception:
            self.reply("I couldn't get the weather right now.")

    # ---------------------------------------------------------------- misc
    def joke(self):
        if pyjokes:
            return self.reply(pyjokes.get_joke())
        self.reply("Why do programmers prefer dark mode? Because light attracts bugs!")

    def open_thing(self, name):
        name = re.sub(r"^(the|my)\s+", "", name).rstrip(".")
        if name in WEBSITES:
            webbrowser.open(WEBSITES[name])
            return self.reply(f"Opening {name}.")
        if name in APPS:
            app = APPS[name].get(SYSTEM)
            try:
                if SYSTEM == "Windows":
                    subprocess.Popen(app if app.startswith("start") else f'start "" {app}', shell=True)
                elif SYSTEM == "Darwin":
                    subprocess.Popen(["open", "-a", app])
                else:
                    subprocess.Popen([app])
                return self.reply(f"Opening {name}.")
            except Exception:
                return self.reply(f"I couldn't open {name} on this computer.")
        # unknown -> try it as a website
        domain = name.replace(" ", "")
        if "." not in domain:
            domain += ".com"
        webbrowser.open(f"https://{domain}")
        self.reply(f"Opening {domain}.")

    def calculate(self, t):
        expr = words_to_math(re.sub(r"^(calculate|compute|what'?s|what is)\s*", "", t))
        try:
            result = safe_eval(expr)
            if isinstance(result, float):
                result = round(result, 6)
                if result.is_integer():
                    result = int(result)
            self.reply(f"{expr} = {result}")
        except Exception:
            self.reply(self.ai.ask(t))            # let the AI handle wordy maths

    def take_note(self, note):
        if not note:
            return self.reply("What should I write down? Say 'take a note' followed by the note.")
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M}] {note}\n")
        self.reply(f"Noted: {note}")

    def read_notes(self):
        if not os.path.exists(NOTES_FILE) or os.path.getsize(NOTES_FILE) == 0:
            return self.reply("You don't have any notes yet.")
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = [line.split("] ", 1)[-1].strip() for line in f if line.strip()]
        last = notes[-5:]
        self.reply(f"You have {len(notes)} notes. The latest: " + "; ".join(last))

    def clear_notes(self):
        open(NOTES_FILE, "w").close()
        self.reply("All notes cleared.")

    def set_timer(self, amount, unit):
        seconds = amount * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
        label = f"{amount} {unit}{'s' if amount != 1 and not unit.endswith('s') else ''}"
        threading.Timer(seconds, lambda: self.reply(f"⏰ Time's up! Your {label} timer is done.")).start()
        self.reply(f"Timer set for {label}.")

    def screenshot(self):
        try:
            import pyautogui                       # lazy import
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            path = os.path.join(SCREENSHOT_DIR, f"screenshot_{dt.datetime.now():%Y%m%d_%H%M%S}.png")
            pyautogui.screenshot(path)
            self.reply(f"Screenshot saved to {path}", speak=False)
            self.speaker.say("Screenshot saved to your Pictures folder.")
        except Exception as e:
            self.reply(f"I couldn't take a screenshot: {e}")

    def system_status(self):
        if not psutil:
            return self.reply("Install psutil to check system status.")
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        msg = f"CPU is at {cpu}% and memory at {ram}%."
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            msg += f" Battery is {int(bat.percent)}%{' and charging' if bat.power_plugged else ''}."
        self.reply(msg)

    def exit(self):
        self.reply(f"Goodbye, {USER_NAME}! Have a great day.")
        self.speaker.wait()
        self.on_exit()


# =============================================================================
# GUI (tkinter - comes with Python)
# =============================================================================
def run_gui():
    import tkinter as tk
    from tkinter import scrolledtext

    BG, PANEL, ACCENT, TEXT, MUTED = "#0f1117", "#181b24", "#00d4ff", "#e6e6e6", "#8b8fa3"
    USER_C, BOT_C = "#7cf29a", "#00d4ff"

    root = tk.Tk()
    root.title(f"{ASSISTANT_NAME} – Voice Assistant")
    root.geometry("720x640")
    root.minsize(520, 480)
    root.configure(bg=BG)

    ui_q = queue.Queue()  # thread-safe channel: worker threads -> GUI

    # ---- header
    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=16, pady=(14, 6))
    tk.Label(header, text=f"◉ {ASSISTANT_NAME.upper()}", font=("Segoe UI", 20, "bold"),
             fg=ACCENT, bg=BG).pack(side="left")
    status = tk.Label(header, text="Ready", font=("Segoe UI", 11), fg=MUTED, bg=BG)
    status.pack(side="right")

    # ---- chat log
    chat = scrolledtext.ScrolledText(root, wrap="word", font=("Segoe UI", 11), bg=PANEL, fg=TEXT,
                                     insertbackground=TEXT, relief="flat", padx=12, pady=10,
                                     state="disabled")
    chat.pack(fill="both", expand=True, padx=16, pady=6)
    chat.tag_config("user_name", foreground=USER_C, font=("Segoe UI", 11, "bold"))
    chat.tag_config("bot_name", foreground=BOT_C, font=("Segoe UI", 11, "bold"))
    chat.tag_config("time", foreground=MUTED, font=("Segoe UI", 8))

    # ---- input row
    bottom = tk.Frame(root, bg=BG)
    bottom.pack(fill="x", padx=16, pady=(6, 14))
    entry = tk.Entry(bottom, font=("Segoe UI", 12), bg=PANEL, fg=TEXT, insertbackground=TEXT,
                     relief="flat")
    entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))

    def btn(parent, text, cmd, color=ACCENT):
        return tk.Button(parent, text=text, command=cmd, font=("Segoe UI", 11, "bold"), bg=color,
                         fg="#000", activebackground=TEXT, relief="flat", padx=14, pady=6,
                         cursor="hand2")

    # ---- callbacks from the assistant (any thread) -> queue
    assistant = Assistant(
        on_message=lambda s, m: ui_q.put(("msg", s, m)),
        on_status=lambda s: ui_q.put(("status", s)),
        on_exit=lambda: ui_q.put(("exit",)),
    )
    continuous = tk.BooleanVar(value=False)

    def pump():
        while not ui_q.empty():
            item = ui_q.get()
            if item[0] == "msg":
                _, sender, msg = item
                chat.configure(state="normal")
                tag = "user_name" if sender == USER_NAME else "bot_name"
                chat.insert("end", f"{sender}  ", tag)
                chat.insert("end", f"{dt.datetime.now():%H:%M}\n", "time")
                chat.insert("end", f"{msg}\n\n")
                chat.configure(state="disabled")
                chat.see("end")
            elif item[0] == "status":
                status.config(text=item[1], fg=ACCENT if item[1] != "Ready" else MUTED)
            elif item[0] == "exit":
                root.destroy()
                return
        root.after(100, pump)

    def send_text(_=None):
        text = entry.get().strip()
        if text:
            entry.delete(0, "end")
            assistant.voice_turn = False
            threading.Thread(target=assistant.process, args=(text,), daemon=True).start()

    def mic_once():
        assistant.voice_turn = True
        threading.Thread(target=assistant.listen_once, daemon=True).start()

    def continuous_loop():
        assistant.voice_turn = False   # the loop itself handles follow-up questions
        while continuous.get():
            assistant.listen_once()

    def toggle_continuous():
        if continuous.get():
            threading.Thread(target=continuous_loop, daemon=True).start()

    btn(bottom, "Send", send_text, "#3a3f55").pack(side="left", padx=(0, 8))
    mic_btn = btn(bottom, "🎤 Speak", mic_once)
    mic_btn.pack(side="left")
    entry.bind("<Return>", send_text)

    opts = tk.Frame(root, bg=BG)
    opts.pack(fill="x", padx=16, pady=(0, 10))
    tk.Checkbutton(opts, text="Hands-free (keep listening)", variable=continuous,
                   command=toggle_continuous, bg=BG, fg=MUTED, selectcolor=PANEL,
                   activebackground=BG, activeforeground=TEXT,
                   font=("Segoe UI", 10)).pack(side="left")
    tk.Label(opts, text="Say or type 'help' for commands", fg=MUTED, bg=BG,
             font=("Segoe UI", 10)).pack(side="right")

    if not assistant.listener.available:
        mic_btn.config(state="disabled", text="🎤 No mic")

    root.bind("<F2>", lambda e: mic_once())          # F2 = push to talk
    root.after(100, pump)
    root.after(300, lambda: threading.Thread(target=assistant.greet, daemon=True).start())
    entry.focus()
    root.mainloop()


# =============================================================================
# CLI MODE (no window)
# =============================================================================
def run_cli():
    stop = threading.Event()
    assistant = Assistant(
        on_message = lambda s, m: print(f"\n{s}: {m}"),
        on_status=lambda s: print(f"   [{s}]") if s != "Ready" else None,
        on_exit=stop.set,
    )
    assistant.voice_turn = False
    assistant.greet()
    print("\nPress Enter to speak, or type a command and press Enter. Ctrl+C to quit.")
    while not stop.is_set():
        try:
            typed = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if typed:
            assistant.process(typed)
        elif assistant.listener.available:
            assistant.listen_once()
        else:
            print("No microphone available — please type.")
        assistant.speaker.wait()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        run_cli()
    else:
        run_gui()

