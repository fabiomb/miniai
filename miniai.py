#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
miniai - cliente de chat ligero para LLM externos (Claude, ChatGPT, Gemini).

Pensado para maquinas viejas: solo usa la biblioteca estandar de Python
(sin dependencias externas, sin compilar nada, sin navegador). Corre en una
terminal, guarda cada chat como un archivo JSON independiente y muestra el
consumo de tokens y el tamano de la ventana de contexto.

Compatible con Python 3.9+.
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.request
import urllib.error

# ----------------------------------------------------------------------------
# Rutas y configuracion
# ----------------------------------------------------------------------------

APP = "miniai"
VERSION = "0.6"

def _xdg(env, default):
    base = os.environ.get(env)
    if not base:
        base = os.path.join(os.path.expanduser("~"), default)
    return os.path.join(base, APP)

CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CHATS_DIR = os.path.join(DATA_DIR, "chats")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# URL de la ultima version del script para /update (raw de GitHub).
# Se puede pisar con "update_url" en config.json.
UPDATE_URL = "https://raw.githubusercontent.com/fabiomb/miniai/main/miniai.py"

# Modelos por defecto (editables en config.json o con /model).
DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "openai": "gpt-5-5",
    "gemini": "gemini-3.5-flash",
}

# Ventana de contexto aproximada por familia de modelo (en tokens), para el
# calculo del porcentaje de uso. Si un modelo no coincide se usa el fallback.
CONTEXT_LIMITS = {
    "claude": 200_000,
    "gpt-5": 256_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gpt-3.5": 16_000,
    "o1": 128_000,
    "gemini-3": 1_000_000,
    "gemini-2": 1_000_000,
    "gemini-1.5": 1_000_000,
    "gemini": 32_000,
}
CONTEXT_FALLBACK = 32_000

DEFAULT_CONFIG = {
    "provider": "claude",
    "models": dict(DEFAULT_MODELS),
    "keys": {"claude": "", "openai": "", "gemini": ""},
    "max_tokens": 4096,
    "timeout": 120,
    "warn_ratio": 0.75,  # avisa cuando el contexto supera este % del limite
    "stream": True,      # imprime la respuesta a medida que llega
}

ENV_KEYS = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

PROVIDERS = ("claude", "openai", "gemini")

# ----------------------------------------------------------------------------
# Colores ANSI (se desactivan si no hay TTY o NO_COLOR)
# ----------------------------------------------------------------------------

class C:
    on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    def _c(code):
        return (lambda s: ("\033[%sm%s\033[0m" % (code, s)) if C.on else str(s))
    dim = staticmethod(_c("2"))
    bold = staticmethod(_c("1"))
    red = staticmethod(_c("31"))
    green = staticmethod(_c("32"))
    yellow = staticmethod(_c("33"))
    blue = staticmethod(_c("34"))
    cyan = staticmethod(_c("36"))
    mag = staticmethod(_c("35"))

# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CHATS_DIR, exist_ok=True)

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # copia profunda
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except (ValueError, OSError) as e:
            warn("No se pudo leer config (%s); uso valores por defecto." % e)
    return cfg

def save_config(cfg):
    ensure_dirs()
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)

def get_key(cfg, provider):
    """Clave desde config o desde variables de entorno."""
    k = (cfg.get("keys") or {}).get(provider) or ""
    if k.strip():
        return k.strip()
    for env in ENV_KEYS.get(provider, ()):
        v = os.environ.get(env)
        if v:
            return v.strip()
    return ""

def estimate_tokens(text):
    """Estimacion barata (~4 chars por token) sin dependencias externas."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)

def context_limit(model):
    for prefix, lim in CONTEXT_LIMITS.items():
        if model.startswith(prefix):
            return lim
    return CONTEXT_FALLBACK

def fmt(n):
    return "{:,}".format(int(n)).replace(",", ".")

# ----------------------------------------------------------------------------
# Presentacion: marco, sangria y ajuste de linea
# ----------------------------------------------------------------------------

MIN_WIDTH = 40
MAX_WIDTH = 100
INDENT = "  "

def term_width():
    """Ancho util de la terminal, acotado para que el texto siga siendo legible."""
    try:
        w = shutil.get_terminal_size().columns
    except (OSError, ValueError):
        w = 80
    return max(MIN_WIDTH, min(MAX_WIDTH, w - 1))

def _unicode_ok():
    """True si la consola puede mostrar los caracteres de dibujo de cajas.
    Las consolas viejas (Linux en modo texto, cmd con cp850) no pueden."""
    enc = sys.stdout.encoding or ""
    if "utf" in enc.lower():
        return True
    try:
        "─│╭·".encode(enc or "ascii")
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False

_UNI = _unicode_ok()
H = "─" if _UNI else "-"          # linea horizontal
V = "│" if _UNI else "|"          # linea vertical
TL = "╭" if _UNI else "+"         # esquinas
TR = "╮" if _UNI else "+"
BL = "╰" if _UNI else "+"
BR = "╯" if _UNI else "+"
DOT = "·" if _UNI else "-"        # separador entre datos
ELLIPSIS = "…" if _UNI else "..."
BAR_F = "█" if _UNI else "#"      # barra de uso de contexto
BAR_E = "░" if _UNI else "-"

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

def vlen(s):
    """Largo visible de una cadena, sin contar los codigos de color."""
    return len(ANSI_RE.sub("", s))

def _fit(s, width):
    """Lleva una linea (con o sin color) al ancho exacto pedido."""
    n = vlen(s)
    if n <= width:
        return s + " " * (width - n)
    plain = ANSI_RE.sub("", s)
    return plain[:max(0, width - len(ELLIPSIS))] + ELLIPSIS

def box(lines, title=None, color=None):
    """Caja del ancho de la terminal. Las lineas pueden traer color."""
    w = term_width()
    inner = w - 4
    paint = color or (lambda s: s)
    if title:
        t = " %s " % title
        top = TL + H + t + H * max(0, w - 3 - vlen(t)) + TR
    else:
        top = TL + H * (w - 2) + TR
    out = [paint(top)]
    for l in lines:
        out.append("%s %s %s" % (paint(V), _fit(l, inner), paint(V)))
    out.append(paint(BL + H * (w - 2) + BR))
    return "\n".join(out)

def rule(label, color=None):
    """Separador con etiqueta: '  Claude --------------------'."""
    paint = color or (lambda s: s)
    text = "%s " % label
    fill = max(3, term_width() - len(INDENT) - vlen(text) - 1)
    return INDENT + paint(text) + C.dim(H * fill)


class LiveWrap:
    """Imprime texto que llega de a trozos, con sangria y ajuste al ancho.

    Respeta los saltos de linea del original, conserva la sangria de cada linea
    (listas, codigo) y no reacomoda lo que esta dentro de un bloque ```."""

    def __init__(self, width=None, indent=INDENT):
        self.indent = indent
        self.avail = max(20, (width or term_width()) - len(indent))
        self.col = 0        # columnas escritas en la linea visual actual
        self.word = ""      # palabra en construccion
        self.space = False  # hay un espacio pendiente entre palabras
        self.bol = True     # estamos al comienzo de una linea visual
        self.lead = True    # seguimos en la sangria inicial de la linea logica
        self.fence = False  # dentro de un bloque de codigo ```
        self.line = ""      # linea logica actual (para detectar el ```)
        self.hang = ""      # sangria de la linea logica (viñetas, listas)
        self.hang_on = False

    def _put(self, s):
        if self.bol:
            sys.stdout.write(self.indent)
            if self.hang_on:
                sys.stdout.write(self.hang)  # continuacion colgada de la viñeta
                self.hang_on = False
            self.bol = False
        sys.stdout.write(s)
        self.col += len(s)
        self.line += s

    def _wrap(self):
        """Corte por ancho; no existe en el texto original."""
        sys.stdout.write("\n")
        # la continuacion arranca alineada con la sangria de la linea logica,
        # siempre que no se coma media pantalla
        keep = self.hang if len(self.hang) < self.avail // 2 else ""
        self.hang_on = bool(keep)
        self.col = len(keep)
        self.bol = True
        self.space = False

    def _newline(self):
        """Salto de linea real del texto."""
        sys.stdout.write("\n")
        if self.line.strip().startswith("```"):
            self.fence = not self.fence
        self.col = 0
        self.bol = True
        self.lead = True
        self.space = False
        self.line = ""
        self.hang = ""
        self.hang_on = False

    def _flush_word(self):
        if not self.word:
            return
        w, self.word = self.word, ""
        if self.col and self.col + (1 if self.space else 0) + len(w) > self.avail:
            self._wrap()
        elif self.space and self.col:
            self._put(" ")
        self.space = False
        self._put(w)

    def feed(self, text):
        for ch in text:
            if ch == "\n":
                self._flush_word()
                self._newline()
            elif self.fence:
                self._put(ch)
            elif ch in " \t":
                if self.lead:
                    self._put(ch)      # sangria inicial: se conserva tal cual
                    self.hang += ch
                else:
                    self._flush_word()
                    self.space = True
            else:
                self.lead = False
                self.word += ch
                if len(self.word) >= self.avail:
                    self._flush_word()  # palabra mas larga que la linea
        sys.stdout.flush()

    def close(self):
        """Cierra la salida dejando siempre una sola linea en blanco al final."""
        self._flush_word()
        sys.stdout.write("\n" if self.bol else "\n\n")
        sys.stdout.flush()

# ----------------------------------------------------------------------------

def parse_version(s):
    """'0.3' -> (0, 3); None si no se puede interpretar."""
    try:
        return tuple(int(x) for x in s.strip().split("."))
    except (ValueError, AttributeError):
        return None

def stdin_pending(wait):
    """True si ya hay mas entrada esperando en stdin.

    Justo despues de un Enter, solo un pegado multilinea deja datos ya
    disponibles; una persona tipeando no llega en esa ventana tan corta.
    Solo tiene sentido con stdin interactivo (TTY)."""
    if not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import msvcrt
        end = time.time() + wait
        while True:
            if msvcrt.kbhit():
                return True
            if time.time() >= end:
                return False
            time.sleep(0.005)
    else:
        import select
        try:
            r, _, _ = select.select([sys.stdin], [], [], wait)
        except (OSError, ValueError):
            return False
        return bool(r)

def warn(msg):
    print(C.yellow("! " + msg), file=sys.stderr)

def err(msg):
    print(C.red("x " + msg), file=sys.stderr)

# ----------------------------------------------------------------------------
# Modelo de datos: Chat
# ----------------------------------------------------------------------------

class Chat:
    def __init__(self, data, path):
        self.data = data
        self.path = path

    @property
    def id(self):
        return self.data["id"]

    @property
    def title(self):
        return self.data.get("title") or "(sin titulo)"

    @property
    def messages(self):
        return self.data.setdefault("messages", [])

    @property
    def system(self):
        return self.data.get("system", "")

    def save(self):
        self.data["updated"] = time.time()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # --- calculos de contexto / consumo -----------------------------------

    def estimated_context_tokens(self):
        total = estimate_tokens(self.system)
        for m in self.messages:
            total += estimate_tokens(m.get("content", "")) + 4
        return total

    def last_input_tokens(self):
        for m in reversed(self.messages):
            u = m.get("usage")
            if u and u.get("input") and not u.get("estimated"):
                return u["input"]
        return None

    def totals(self):
        ti = to = 0
        for m in self.messages:
            u = m.get("usage") or {}
            ti += u.get("input", 0) or 0
            to += u.get("output", 0) or 0
        return ti, to


def new_chat(provider, model, title=None):
    ensure_dirs()
    cid = time.strftime("%Y%m%d-%H%M%S")
    # evita colisiones si se crean dos en el mismo segundo
    base = os.path.join(CHATS_DIR, cid)
    path = base + ".json"
    n = 1
    while os.path.exists(path):
        path = "%s-%d.json" % (base, n)
        n += 1
    data = {
        "id": os.path.splitext(os.path.basename(path))[0],
        "title": title or "Chat nuevo",
        "provider": provider,
        "model": model,
        "system": "",
        "created": time.time(),
        "updated": time.time(),
        "messages": [],
    }
    ch = Chat(data, path)
    ch.save()
    return ch

def list_chats():
    ensure_dirs()
    out = []
    for name in os.listdir(CHATS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CHATS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append(Chat(data, path))
        except (ValueError, OSError):
            continue
    out.sort(key=lambda c: c.data.get("updated", 0), reverse=True)
    return out

# ----------------------------------------------------------------------------
# Proveedores LLM (REST via urllib)
# ----------------------------------------------------------------------------

class ProviderError(Exception):
    pass

def _http_error(e):
    detail = ""
    try:
        detail = e.read().decode("utf-8")
        j = json.loads(detail)
        detail = j.get("error", {}).get("message") or j.get("error") or detail
    except Exception:
        pass
    return ProviderError("HTTP %s: %s" % (e.code, detail))

def _http_post(url, headers, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    except urllib.error.URLError as e:
        raise ProviderError("Red: %s" % e.reason)
    except TimeoutError:
        raise ProviderError("Tiempo de espera agotado.")

def _sse_data(url, headers, payload, timeout):
    """Generador de objetos JSON a partir de una respuesta SSE (lineas 'data:')."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    except urllib.error.URLError as e:
        raise ProviderError("Red: %s" % e.reason)
    except TimeoutError:
        raise ProviderError("Tiempo de espera agotado.")
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    break
                continue
            try:
                yield json.loads(data)
            except ValueError:
                continue


def call_claude(key, model, system, messages, max_tokens, timeout):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    if system:
        payload["system"] = system
    data = _http_post(url, headers, payload, timeout)
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    u = data.get("usage") or {}
    usage = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}
    return text, usage


def call_openai(key, model, system, messages, max_tokens, timeout):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + key,
        "content-type": "application/json",
    }
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    for m in messages:
        msgs.append({"role": m["role"], "content": m["content"]})
    payload = {"model": model, "messages": msgs, "max_tokens": max_tokens}
    data = _http_post(url, headers, payload, timeout)
    choices = data.get("choices") or []
    text = choices[0]["message"]["content"] if choices else ""
    u = data.get("usage") or {}
    usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
    return text, usage


def call_gemini(key, model, system, messages, max_tokens, timeout):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
           % (model, key))
    headers = {"content-type": "application/json"}
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _http_post(url, headers, payload, timeout)
    cands = data.get("candidates") or []
    text = ""
    if cands:
        for p in cands[0].get("content", {}).get("parts", []):
            text += p.get("text", "")
    u = data.get("usageMetadata") or {}
    usage = {"input": u.get("promptTokenCount", 0), "output": u.get("candidatesTokenCount", 0)}
    return text, usage


# --- variantes con streaming (SSE) -----------------------------------------
# Cada generador entrega tuplas ("text", fragmento) a medida que llegan, y una
# tupla final ("usage", {"input": n, "output": n}).

def stream_claude(key, model, system, messages, max_tokens, timeout):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": True,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    if system:
        payload["system"] = system
    usage = {"input": 0, "output": 0}
    for obj in _sse_data(url, headers, payload, timeout):
        t = obj.get("type")
        if t == "content_block_delta":
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                yield ("text", d["text"])
        elif t == "message_start":
            u = (obj.get("message") or {}).get("usage") or {}
            usage["input"] = u.get("input_tokens", 0)
            yield ("usage", dict(usage))  # tokens de entrada ya conocidos
        elif t == "message_delta":
            u = obj.get("usage") or {}
            if u.get("output_tokens"):
                usage["output"] = u["output_tokens"]
        elif t == "error":
            msg = (obj.get("error") or {}).get("message", "error de la API")
            raise ProviderError(msg)
    yield ("usage", usage)


def stream_openai(key, model, system, messages, max_tokens, timeout):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": "Bearer " + key, "content-type": "application/json"}
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    for m in messages:
        msgs.append({"role": m["role"], "content": m["content"]})
    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    usage = {"input": 0, "output": 0}
    for obj in _sse_data(url, headers, payload, timeout):
        choices = obj.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            c = delta.get("content")
            if c:
                yield ("text", c)
        u = obj.get("usage")
        if u:
            usage["input"] = u.get("prompt_tokens", 0)
            usage["output"] = u.get("completion_tokens", 0)
    yield ("usage", usage)


def stream_gemini(key, model, system, messages, max_tokens, timeout):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:streamGenerateContent"
           "?alt=sse&key=%s" % (model, key))
    headers = {"content-type": "application/json"}
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    usage = {"input": 0, "output": 0}
    for obj in _sse_data(url, headers, payload, timeout):
        for cand in obj.get("candidates") or []:
            for p in (cand.get("content") or {}).get("parts", []):
                if p.get("text"):
                    yield ("text", p["text"])
        u = obj.get("usageMetadata")
        if u:
            usage["input"] = u.get("promptTokenCount", 0)
            usage["output"] = u.get("candidatesTokenCount", 0)
    yield ("usage", usage)


PROVIDER_CALLS = {
    "claude": call_claude,
    "openai": call_openai,
    "gemini": call_gemini,
}

PROVIDER_STREAMS = {
    "claude": stream_claude,
    "openai": stream_openai,
    "gemini": stream_gemini,
}

PROVIDER_LABEL = {"claude": "Claude", "openai": "ChatGPT", "gemini": "Gemini"}

# ----------------------------------------------------------------------------
# Aplicacion / REPL
# ----------------------------------------------------------------------------

LOGO = r"""
 __  __ _       _    _    ___
|  \/  (_)_ __ (_)  / \  |_ _|
| |\/| | | '_ \| | / _ \  | |
| |  | | | | | | |/ ___ \ | |
|_|  |_|_|_| |_|_/_/   \_\___|
""".strip("\n")

TAGLINE = "Claude / ChatGPT / Gemini en tu terminal"
BANNER = "miniai v%s - %s" % (VERSION, TAGLINE)

def clear_screen():
    """Limpia la terminal (solo si es interactiva)."""
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")

HELP_GROUPS = [
    ("Chats", [
        ("/new [titulo]", "crear un chat nuevo"),
        ("/chats", "listar los chats"),
        ("/switch <n>", "cambiar al chat n (numero de la lista)"),
        ("/rename <titulo>", "renombrar el chat actual"),
        ("/delete <n>", "borrar el chat n"),
        ("/clear", "vaciar los mensajes del chat actual"),
        ("/trim <n>", "conservar solo los ultimos n mensajes"),
    ]),
    ("Mensajes", [
        ("/paste", "mensaje multilinea (termina con un . solo)"),
        ("/system [texto]", "ver o fijar el prompt de sistema del chat"),
        ("/max <n>", "fijar max_tokens de respuesta"),
        ("/stream [on|off]", "respuesta en vivo (streaming)"),
    ]),
    ("Modelo", [
        ("/provider <nombre>", "cambiar proveedor: claude | openai | gemini"),
        ("/model [nombre]", "ver o cambiar el modelo del chat actual"),
        ("/keys", "estado de las claves API"),
        ("/setkey <prov> <k>", "guardar una clave API en la config"),
    ]),
    ("Sistema", [
        ("/status", "consumo y tamano del contexto del chat actual"),
        ("/update", "bajar la ultima version del script desde GitHub"),
        ("/help", "esta ayuda"),
        ("/quit", "salir"),
    ]),
]

HELP_NOTES = [
    "Cualquier otra linea se envia como mensaje al chat actual.",
    "Si pegas varias lineas de una vez, se juntan en un solo mensaje.",
    "Vigila el pie de pantalla para que el contexto no crezca de mas.",
]

def help_text():
    """Ayuda agrupada por tema y alineada al ancho de la terminal."""
    pad = max(len(c) for _, items in HELP_GROUPS for c, _ in items) + 2
    out = []
    for group, items in HELP_GROUPS:
        out.append("")
        out.append(rule(group, C.bold))
        for cmd, desc in items:
            out.append(INDENT + C.cyan(cmd.ljust(pad)) + C.dim(desc))
    out.append("")
    for n in HELP_NOTES:
        out.append(INDENT + C.dim(n))
    return "\n".join(out)


class App:
    def __init__(self):
        self.cfg = load_config()
        self.chat = None
        self._partial = ("", {"input": 0, "output": 0})
        self._readline = False

    def prompt(self):
        """Prompt del REPL. Con readline hay que marcar los codigos de color
        (\\001 .. \\002) para que la edicion de linea calcule bien el ancho."""
        if not C.on:
            return ">> "
        if self._readline:
            return "\001\033[34m\002>> \001\033[0m\002"
        return "\033[34m>> \033[0m"

    # --- barra de estado ---------------------------------------------------

    def status_line(self, chat=None):
        c = chat or self.chat
        if not c:
            return ""
        provider = c.data.get("provider", self.cfg["provider"])
        model = c.data.get("model", "")
        est = c.estimated_context_tokens()
        real = c.last_input_tokens()
        shown = real if real else est
        limit = context_limit(model)
        ratio = shown / limit if limit else 0
        pct = "%.1f%%" % (ratio * 100)
        color = C.green
        if ratio >= self.cfg["warn_ratio"]:
            color = C.red
        elif ratio >= self.cfg["warn_ratio"] * 0.7:
            color = C.yellow
        origen = "real" if real else "~est"
        ti, to = c.totals()
        parts = [
            C.dim("[") + C.cyan("%s %s %s" % (PROVIDER_LABEL.get(provider, provider),
                                              DOT, model)) + C.dim("]"),
            "msgs " + C.bold(str(len(c.messages))),
            "ctx " + color("%s (%s)" % (fmt(shown), pct)),
            C.dim("de %s %s" % (fmt(limit), origen)),
            C.dim("total ^%s v%s" % (fmt(ti), fmt(to))),
        ]
        # en terminales angostas se va soltando lo menos importante
        sep = C.dim(" | ")
        while len(parts) > 2 and vlen(sep.join(parts)) > term_width():
            parts.pop()
        return sep.join(parts)

    def footer(self):
        """Pie de pantalla, justo encima del prompt."""
        s = self.status_line()
        if s:
            print(s)

    # --- envio de mensajes -------------------------------------------------

    def send(self, text):
        c = self.chat
        provider = c.data.get("provider", self.cfg["provider"])
        model = c.data.get("model") or DEFAULT_MODELS.get(provider)
        key = get_key(self.cfg, provider)
        if not key:
            envs = " o ".join(ENV_KEYS.get(provider, ()))
            err("Falta la clave de %s. Define %s o usa /setkey %s <clave>."
                % (PROVIDER_LABEL.get(provider, provider), envs, provider))
            return

        # aviso preventivo de contexto grande
        limit = context_limit(model)
        if c.estimated_context_tokens() > limit * self.cfg["warn_ratio"]:
            warn("El contexto esta cerca del limite (%s). Usa /trim o /new para achicarlo."
                 % fmt(limit))

        c.messages.append({"role": "user", "content": text, "ts": time.time()})
        label = PROVIDER_LABEL.get(provider, provider)
        args = (key, model, c.system, c.messages, self.cfg["max_tokens"], self.cfg["timeout"])
        t0 = time.time()
        interrupted = False
        try:
            if self.cfg.get("stream", True):
                reply, usage = self._stream(provider, label, args)
            else:
                print(C.dim(INDENT + "... consultando %s ..." % label))
                reply, usage = PROVIDER_CALLS[provider](*args)
                print()
                print(rule(label, lambda s: C.bold(C.green(s))))
                out = LiveWrap()
                out.feed(reply.strip())
                out.close()
        except ProviderError as e:
            c.messages.pop()  # revierte el mensaje del usuario
            err(str(e))
            return
        except KeyboardInterrupt:
            interrupted = True
            reply, usage = self._partial
            usage = dict(usage)
            # la API no alcanzo a informar el consumo: lo estimamos
            if not usage.get("output"):
                usage["output"] = estimate_tokens(reply)
            if not usage.get("input"):
                usage["input"] = c.estimated_context_tokens()
            usage["estimated"] = True
            print(C.dim("\n" + INDENT + "(respuesta interrumpida; tokens estimados)"))
        dt = time.time() - t0

        if not reply.strip() and not interrupted:
            c.messages.pop()
            warn("Respuesta vacia del proveedor.")
            return

        c.messages.append({
            "role": "assistant", "content": reply, "ts": time.time(),
            "usage": usage,
        })
        # titula el chat con el primer mensaje si aun es el titulo por defecto
        if c.data.get("title") in (None, "", "Chat nuevo") and len(c.messages) <= 2:
            c.data["title"] = (text.strip().splitlines() or ["Chat"])[0][:40]
        c.save()

        approx = "~" if usage.get("estimated") else ""
        print(INDENT + C.dim("(%s%s tok entrada, %s%s salida, %.1fs)"
                             % (approx, fmt(usage.get("input", 0)),
                                approx, fmt(usage.get("output", 0)), dt)))
        print()

    def _stream(self, provider, label, args):
        """Imprime la respuesta en vivo y devuelve (texto_completo, usage).
        Guarda lo parcial en self._partial por si el usuario corta con Ctrl-C."""
        gen = PROVIDER_STREAMS[provider](*args)
        usage = {"input": 0, "output": 0}
        chunks = []
        self._partial = ("", usage)
        out = LiveWrap()
        started = False
        try:
            for kind, val in gen:
                if kind == "text":
                    if not started:
                        print()
                        print(rule(label, lambda s: C.bold(C.green(s))))
                        started = True
                    chunks.append(val)
                    out.feed(val)
                    self._partial = ("".join(chunks), usage)
                elif kind == "usage":
                    usage = val
                    self._partial = ("".join(chunks), usage)
        finally:
            if started:
                out.close()
        self._partial = ("".join(chunks), usage)
        return "".join(chunks), usage

    # --- comandos ----------------------------------------------------------

    def cmd(self, line):
        parts = line[1:].split(None, 1)
        name = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if name in ("quit", "q", "exit"):
            return False
        elif name in ("help", "h", "?"):
            print(help_text())
        elif name == "new":
            self.chat = new_chat(self.cfg["provider"],
                                 self.cfg["models"].get(self.cfg["provider"]),
                                 arg or None)
            print(C.green("Nuevo chat: ") + self.chat.title + C.dim("  [%s]" % self.chat.id))
        elif name in ("chats", "list", "ls"):
            self.list_chats()
        elif name in ("switch", "sw", "cd"):
            self.switch(arg)
        elif name == "rename":
            if not self.require_chat():
                return True
            if arg:
                self.chat.data["title"] = arg
                self.chat.save()
                print(C.green("Renombrado a: ") + arg)
            else:
                err("Uso: /rename <titulo>")
        elif name in ("delete", "del", "rm"):
            self.delete(arg)
        elif name == "clear":
            if self.require_chat():
                self.chat.messages.clear()
                self.chat.save()
                print(C.green("Mensajes borrados."))
        elif name == "trim":
            self.trim(arg)
        elif name == "system":
            self.system_cmd(arg)
        elif name == "paste":
            self.paste_mode()
        elif name in ("provider", "prov"):
            self.set_provider(arg)
        elif name == "model":
            self.set_model(arg)
        elif name == "max":
            self.set_max(arg)
        elif name == "stream":
            self.set_stream(arg)
        elif name in ("status", "st"):
            self.status_full()
        elif name == "keys":
            self.show_keys()
        elif name == "setkey":
            self.set_key(arg)
        elif name == "update":
            self.update_self()
        else:
            err("Comando desconocido: /%s  (usa /help)" % name)
        return True

    def require_chat(self):
        if not self.chat:
            err("No hay chat activo. Usa /new o /switch.")
            return False
        return True

    def list_chats(self):
        chats = list_chats()
        if not chats:
            print(INDENT + C.dim("(no hay chats; crea uno con /new)"))
            return
        model_w = 18
        # el titulo se queda con lo que sobra despues de las columnas fijas
        title_w = max(10, term_width() - 61)
        print()
        print(rule("Chats %s %d" % (DOT, len(chats)), C.bold))
        for i, c in enumerate(chats, 1):
            mark = C.green("*") if (self.chat and c.id == self.chat.id) else " "
            est = c.estimated_context_tokens()
            when = time.strftime("%d/%m %H:%M", time.localtime(c.data.get("updated", 0)))
            print("%s%s %2d. %s  %s  %s  %s"
                  % (INDENT, mark, i,
                     C.bold(_fit(c.title, title_w)),
                     C.cyan(_fit(c.data.get("model", "?"), model_w)),
                     C.dim(_fit("%d msgs ~%s tok" % (len(c.messages), fmt(est)), 18)),
                     C.dim(when)))
        print()

    def _pick(self, arg):
        chats = list_chats()
        if not arg.isdigit():
            err("Indica el numero de la lista (ver /chats).")
            return None, chats
        idx = int(arg)
        if idx < 1 or idx > len(chats):
            err("Fuera de rango (1..%d)." % len(chats))
            return None, chats
        return chats[idx - 1], chats

    def switch(self, arg):
        c, _ = self._pick(arg)
        if c:
            self.chat = c
            print(C.green("Chat activo: ") + c.title)

    def delete(self, arg):
        c, _ = self._pick(arg)
        if not c:
            return
        try:
            os.remove(c.path)
        except OSError as e:
            err("No se pudo borrar: %s" % e)
            return
        if self.chat and self.chat.id == c.id:
            self.chat = None
        print(C.green("Borrado: ") + c.title)

    def trim(self, arg):
        if not self.require_chat():
            return
        if not arg.isdigit() or int(arg) < 0:
            err("Uso: /trim <n>  (conserva los ultimos n mensajes)")
            return
        n = int(arg)
        msgs = self.chat.messages
        if n < len(msgs):
            del msgs[:len(msgs) - n]
            self.chat.save()
        print(C.green("Conservados %d mensajes." % len(self.chat.messages)))

    def system_cmd(self, arg):
        if not self.require_chat():
            return
        if arg:
            self.chat.data["system"] = arg
            self.chat.save()
            print(C.green("Prompt de sistema actualizado."))
        else:
            s = self.chat.system
            print(C.dim("Sistema: ") + (s if s else C.dim("(vacio)")))

    def paste_mode(self):
        if not self.require_chat():
            return
        print(C.dim("Modo multilinea: escribi o pega el texto. Termina con una "
                    "linea que tenga solo un punto (.) o con Ctrl-D."))
        lines = []
        while True:
            try:
                l = input()
            except EOFError:
                print()
                break
            if l.strip() == ".":
                break
            lines.append(l)
        text = "\n".join(lines).strip()
        if not text:
            print(C.dim("(nada que enviar)"))
            return
        try:
            self.send(text)
        except KeyboardInterrupt:
            print(C.dim("\n(cancelado)"))

    def set_provider(self, arg):
        arg = arg.lower()
        if arg not in PROVIDERS:
            err("Proveedores: %s" % ", ".join(PROVIDERS))
            return
        self.cfg["provider"] = arg
        save_config(self.cfg)
        if self.chat:
            self.chat.data["provider"] = arg
            self.chat.data["model"] = self.cfg["models"].get(arg, DEFAULT_MODELS[arg])
            self.chat.save()
        print(C.green("Proveedor: ") + PROVIDER_LABEL[arg]
              + C.dim("  (modelo %s)" % self.cfg["models"].get(arg)))

    def set_model(self, arg):
        prov = (self.chat.data.get("provider") if self.chat else self.cfg["provider"])
        if not arg:
            cur = self.chat.data.get("model") if self.chat else self.cfg["models"].get(prov)
            print(C.dim("Modelo actual (%s): " % prov) + str(cur))
            print(C.dim("Sugeridos: ") + ", ".join(sorted(set(DEFAULT_MODELS.values()))))
            return
        self.cfg["models"][prov] = arg
        save_config(self.cfg)
        if self.chat:
            self.chat.data["model"] = arg
            self.chat.save()
        print(C.green("Modelo de %s: " % prov) + arg)

    def set_max(self, arg):
        if not arg.isdigit() or int(arg) <= 0:
            err("Uso: /max <n>  (actual: %d)" % self.cfg["max_tokens"])
            return
        self.cfg["max_tokens"] = int(arg)
        save_config(self.cfg)
        print(C.green("max_tokens = %d" % self.cfg["max_tokens"]))

    def set_stream(self, arg):
        a = arg.lower()
        if a in ("on", "si", "1", "true"):
            self.cfg["stream"] = True
        elif a in ("off", "no", "0", "false"):
            self.cfg["stream"] = False
        elif a == "":
            self.cfg["stream"] = not self.cfg.get("stream", True)
        else:
            err("Uso: /stream on|off")
            return
        save_config(self.cfg)
        print(C.green("Streaming %s." % ("activado" if self.cfg["stream"] else "desactivado")))

    def status_full(self):
        if not self.require_chat():
            return
        c = self.chat
        model = c.data.get("model", "")
        limit = context_limit(model)
        est = c.estimated_context_tokens()
        real = c.last_input_tokens()
        ti, to = c.totals()
        print()
        print(rule("%s %s %s" % (c.title, DOT, c.id), C.bold))
        print(INDENT + C.dim("Proveedor : ") + "%s (%s)"
              % (PROVIDER_LABEL.get(c.data.get("provider")), model))
        print(INDENT + C.dim("Mensajes  : ") + "%d" % len(c.messages))
        print(INDENT + C.dim("Contexto  : ") + "~%s tok estimados (limite del modelo %s)"
              % (fmt(est), fmt(limit)))
        if real:
            print(INDENT + C.dim("Contexto  : ") + "%s tok reales (ultima respuesta)" % fmt(real))
        shown = real or est
        bar_w = max(10, min(40, term_width() - 26))
        filled = min(bar_w, int(bar_w * shown / limit)) if limit else 0
        bar = BAR_F * filled + BAR_E * (bar_w - filled)
        pct = (shown / limit * 100) if limit else 0
        color = C.red if pct >= self.cfg["warn_ratio"] * 100 else C.green
        print(INDENT + C.dim("Uso       : ") + color(bar) + " %.1f%%" % pct)
        print(INDENT + C.dim("Consumo   : ") + "%s tok de entrada, %s de salida"
              % (fmt(ti), fmt(to)))
        print(INDENT + C.dim("max_tokens: ") + "%d por respuesta" % self.cfg["max_tokens"])
        print()

    def show_keys(self):
        for p in PROVIDERS:
            k = get_key(self.cfg, p)
            if k:
                masked = k[:4] + "..." + k[-4:] if len(k) > 8 else "****"
                src = "config" if (self.cfg["keys"].get(p) or "").strip() else "entorno"
                print("  %-8s %s  %s" % (PROVIDER_LABEL[p], C.green(masked), C.dim(src)))
            else:
                print("  %-8s %s" % (PROVIDER_LABEL[p], C.red("(sin clave)")))

    def set_key(self, arg):
        bits = arg.split(None, 1)
        if len(bits) != 2 or bits[0].lower() not in PROVIDERS:
            err("Uso: /setkey <claude|openai|gemini> <clave>")
            return
        prov = bits[0].lower()
        self.cfg["keys"][prov] = bits[1].strip()
        save_config(self.cfg)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
        print(C.green("Clave de %s guardada." % PROVIDER_LABEL[prov]))

    def update_self(self):
        """Descarga la ultima version del script y reemplaza el archivo actual.
        Valida la descarga antes de tocar nada y deja copia .bak del anterior."""
        url = self.cfg.get("update_url") or UPDATE_URL
        dest = os.path.realpath(os.path.abspath(__file__))
        print(C.dim("Descargando: %s" % url))
        req = urllib.request.Request(url, headers={"User-Agent": APP})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg["timeout"]) as resp:
                new = resp.read()
        except urllib.error.HTTPError as e:
            err("HTTP %s al descargar la actualizacion." % e.code)
            return
        except urllib.error.URLError as e:
            err("Red: %s" % e.reason)
            return
        except TimeoutError:
            err("Tiempo de espera agotado.")
            return
        # validaciones: que sea texto, que parezca miniai y que compile
        try:
            src = new.decode("utf-8")
        except UnicodeDecodeError:
            err("La descarga no es un archivo de texto valido; no toco nada.")
            return
        if APP not in src:
            err("La descarga no parece ser %s; no toco nada." % APP)
            return
        try:
            compile(src, dest, "exec")
        except SyntaxError as e:
            err("El archivo descargado tiene un error de sintaxis (linea %s); no toco nada."
                % e.lineno)
            return
        try:
            with open(dest, "rb") as f:
                cur = f.read()
        except OSError as e:
            err("No puedo leer el script actual (%s): %s" % (dest, e))
            return
        if new == cur:
            print(C.green("Ya estas en la ultima version (v%s)." % VERSION))
            return
        # compara el numero de version del script remoto con el local
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M)
        remote_v = m.group(1) if m else None
        lv, rv = parse_version(VERSION), parse_version(remote_v)
        if lv and rv:
            if rv > lv:
                print(C.green("Nueva version disponible: v%s (tenes v%s)." % (remote_v, VERSION)))
            elif rv == lv:
                warn("La version publicada es la misma (v%s) pero el contenido difiere." % remote_v)
            else:
                warn("La version publicada (v%s) es MAS VIEJA que la tuya (v%s); "
                     "seguir seria volver atras." % (remote_v, VERSION))
        else:
            warn("No pude determinar la version publicada; comparo solo el contenido.")
        print("  Script actual: %s (v%s, %s bytes)" % (dest, VERSION, fmt(len(cur))))
        print("  Descargado   : v%s, %s bytes" % (remote_v or "?", fmt(len(new))))
        try:
            ans = input("Pisar el archivo actual? (queda copia .bak) [s/N] ")
        except EOFError:
            ans = ""
        if ans.strip().lower() not in ("s", "si", "y", "yes"):
            print(C.dim("(cancelado)"))
            return
        bak = dest + ".bak"
        try:
            with open(bak, "wb") as f:
                f.write(cur)
            tmp = dest + ".upd"
            with open(tmp, "wb") as f:
                f.write(new)
            try:
                os.chmod(tmp, os.stat(dest).st_mode)  # conserva el +x en Linux
            except OSError:
                pass
            os.replace(tmp, dest)
        except OSError as e:
            err("No se pudo actualizar: %s" % e)
            return
        print(C.green("Actualizado.") + C.dim("  (copia anterior en %s)" % bak))
        print(C.dim("Sali y volve a entrar para usar la nueva version."))

    # --- bucle principal ---------------------------------------------------

    def run(self):
        ensure_dirs()
        try:
            import readline  # historial de linea en Linux
            histfile = os.path.join(DATA_DIR, "history")
            try:
                readline.read_history_file(histfile)
            except OSError:
                pass
            import atexit
            atexit.register(lambda: _save_history(readline, histfile))
            self._readline = True
        except ImportError:
            pass

        chats = list_chats()
        if chats:
            self.chat = chats[0]
            primero = False
        else:
            self.chat = new_chat(self.cfg["provider"],
                                 self.cfg["models"].get(self.cfg["provider"]))
            primero = True

        clear_screen()
        print(C.cyan(LOGO))
        print()
        prov = self.chat.data.get("provider", self.cfg["provider"])
        print(box([
            C.dim(TAGLINE),
            C.cyan("%s %s %s" % (PROVIDER_LABEL.get(prov, prov), DOT,
                                 self.chat.data.get("model", "?"))),
            C.dim("Chat: ") + self.chat.title
            + C.dim(" %s %d msgs" % (DOT, len(self.chat.messages))),
        ], title="miniai v%s" % VERSION, color=C.dim))
        print(INDENT + C.dim("/help comandos %s /paste multilinea %s /quit salir"
                             % (DOT, DOT)))
        if primero:
            print(INDENT + C.dim("(se creo tu primer chat)"))
        # aviso si no hay ninguna clave configurada
        if not any(get_key(self.cfg, p) for p in PROVIDERS):
            warn("No hay claves API configuradas. Usa /setkey o define las variables de entorno.")
        print()

        while True:
            self.footer()
            try:
                line = input(self.prompt())
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print(C.dim("\n(Ctrl-C; usa /quit para salir)"))
                continue
            # Si quedaron mas lineas ya disponibles (pegado multilinea),
            # se juntan en un solo mensaje en vez de enviarse por separado.
            while stdin_pending(0.05):
                try:
                    line += "\n" + input()
                except EOFError:
                    break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/") and "\n" not in line:
                if not self.cmd(line):
                    break
            else:
                if not self.require_chat():
                    continue
                try:
                    self.send(line)
                except KeyboardInterrupt:
                    print(C.dim("\n(cancelado)"))
        print(C.dim("Hasta luego."))


def _save_history(readline, histfile):
    try:
        readline.set_history_length(1000)
        readline.write_history_file(histfile)
    except OSError:
        pass


def main():
    try:
        App().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
