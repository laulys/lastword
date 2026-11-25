"""Seguridad de lo que entra en la memoria. Reglas fijas, sin LLM (ADR-0007).

La memoria persiste entre sesiones y la comparten varios clientes, así que lo que entra una
vez se repite en cada sesión y en cada herramienta:

- **Secretos** (claves de API, contraseñas): se rechazan al guardar. El archivo no está
  cifrado y cualquier agente conectado al workspace los recuperaría.
- **Caracteres invisibles** que pueden esconder texto (Unicode *tags*, control
  bidireccional, selectores de variación fuera de sitio) y **códigos de control** que el
  terminal obedece: se rechazan; ninguna memoria los necesita.
- **Texto que se dirige al agente como una orden**, o caracteres de ancho cero: se guarda,
  pero marcado, porque hay frases legítimas parecidas. Las marcas se calculan al leer, así
  que también cubren memorias guardadas antes de esta versión.

Son reglas: detectan lo evidente, no a un atacante que las conozca. La defensa de fondo es
que el agente trate la memoria como datos, y así lo dicen las instrucciones del servidor.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from vigente.errors import HiddenCharactersError, SecretDetectedError
from vigente.i18n import tr


@dataclass(frozen=True)
class Finding:
    code: str  # estable: "secret:stripe", "hidden:tags", "instruction:override"
    label: str  # en español, para mensajes


# --- secretos

_SECRET_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("anthropic", "una clave de API de Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    (
        "openai",
        "una clave de API de OpenAI",
        re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}"),
    ),
    ("aws", "una clave de acceso de AWS", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github",
        "un token de GitHub",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    ),
    (
        "stripe",
        "una clave secreta de Stripe",
        re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    ),
    ("slack", "un token de Slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    (
        "google",
        "una clave de API de Google",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    ),
    (
        "private_key",
        "una clave privada",
        # El bloque entero, para que sus líneas en base64 no cuenten además como tokens.
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
            r"(?:[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|[\s\S]*)"
        ),
    ),
    (
        "jwt",
        "un token JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    (
        "url_credentials",
        "una dirección con usuario y contraseña",
        # El usuario puede faltar: `redis://:contraseña@servidor`.
        re.compile(r"\b[a-z][a-z0-9+.-]{0,20}://[^\s/:@]*:[^\s/@]+@", re.IGNORECASE),
    ),
)

# «la contraseña del wifi es …», «password: …», «API key = …», y también con nombre de variable:
# «DB_PASSWORD=…», «DJANGO_SECRET_KEY=…», «dbPassword: …», y entre comillas o en Markdown:
# «"password": "…"», «**Contraseña:** …». Se busca sin tildes.
_PASSWORD_PHRASE = re.compile(
    r"(?<![A-Za-z0-9_?&#])(?:[A-Za-z0-9]+_){0,4}"  # no un parámetro de URL: van aparte
    r"(?:(?-i:[a-z][a-z0-9]{0,20}(?=[A-Z])))?"  # clientSecret
    r"(?:(?:contrasena|password|passwd|pwd|passphrase|clave|token|secreto|secret|api[ _-]?key)s?"
    r"|(?<=_)pass|(?-i:(?<=[a-z0-9])Pass|\bPASS(?==)))"
    r"(?:_(?:access_key|key|token|secret|password|passwd|pwd|prod|dev|test|staging|local)"
    r"|(?-i:Key|Token))?\b"
    r"(?:\s+(?:actual|nueva|nuevo|vieja|ahora|temporal|maestra|current|new|now|old|temporary))?"
    r"(?:\s+(?:de|del|para|of|for)\s+(?:[^\s:=]+\s+){0,3}?[^\s:=]+)?"
    r"[\"'*`]{0,3}\s*(?:(?:\bes\b|\bera\b|\bis\b)\s?:?|:|=)[*`]{0,3}\s*[\"'«“*`]{0,3}"
    r"(?P<value>[^\s\"'»”*`]{6,})",
    re.IGNORECASE,
)
# Identificadores en minúsculas (id_pedido, color.brand.500): nombres, no secretos.
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
# Un trozo de código: `tokens = word_tokenize(text)`, `token = $(openssl rand)`, `tokens[i + 1]`.
_CODE = re.compile(r".*\(|\w+\[|\$\{|process\.env\b|os\.environ\b")
_NUMBER = re.compile(r"v?\d+(?:[.,/:-]\d+)*")
_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,}(?![A-Za-z0-9+/=_-])")
_MIN_TOKEN_ENTROPY = 4.0  # bits por carácter; 40 caracteres aleatorios rondan los 5
# Las rutas de una URL parecen tokens largos (en LongMemEval, casi todos los falsos avisos):
# dentro de una URL solo cuentan los parámetros con nombre de secreto.
_URL = re.compile(r"\b[a-z][a-z0-9+.-]{0,20}://[^\s<>\"'«»]+", re.IGNORECASE)
_SECRET_QUERY = re.compile(
    r"[?&#](?:access_token|refresh_token|client_secret|api_?key|token|key|secret|sig|signature|"
    r"password|pwd|auth)=(?P<value>[^&#\s]{16,})",
    re.IGNORECASE,
)
_FINGERPRINT = re.compile(r"(?:sha\d+|md5):$", re.IGNORECASE)  # huellas: públicas


# Lo que no se dibuja (Default_Ignorable_Code_Point en Unicode) y los rellenos que se ven como un
# hueco. Pueden partir una palabra para que un detector no la reconozca: se quitan para detectar.
# De estos, los que además se marcan o se rechazan están más abajo.
_IGNORABLE = (
    "\u00ad\u034f\u061c\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f"
    "\ufe00-\ufe0f\ufeff\ufff0-\ufffb\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
    "\U000e0000-\U000e0fff"
)
_BLANK = re.compile("[\u115f\u1160\u3164\uffa0\u2800]")
_INVISIBLE = re.compile(f"[{_IGNORABLE}\u115f\u1160\u3164\uffa0\u2800]")

# Signos que NFKD convierte en muchas letras (㌖ → seis): multiplicarían el texto que hay que
# revisar y no sirven para escribir una orden en español ni en inglés.
_EXPANDING = re.compile("[\u3200-\u321e\u327c\u327d\u3300-\u33ff\ufdf0-\ufdff]")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", _EXPANDING.sub(" ", text))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _looks_like_secret(value: str) -> bool:
    value = value.rstrip(".,;:)]}»”\"'")
    if len(value) < 6 or _NUMBER.fullmatch(value) or _CODE.match(value):
        return False
    if _IDENTIFIER.fullmatch(value) and not _UUID.fullmatch(value):
        return False
    has_letter = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    has_symbol = any(not char.isalnum() for char in value)
    return has_letter and (has_digit or (has_symbol and len(value) >= 8))


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _random_token(token: str) -> bool:
    classes = (
        any(char.isupper() for char in token),
        any(char.islower() for char in token),
        any(char.isdigit() for char in token),
    )
    return all(classes) and _entropy(token) >= _MIN_TOKEN_ENTROPY


def find_secrets(text: str) -> list[Finding]:
    """Un mismo secreto se informa una vez, con el formato más concreto que lo reconoce."""
    text = _INVISIBLE.sub("", text)  # un carácter invisible en medio no esconde una clave
    findings: list[Finding] = []
    found: list[str] = []  # textos ya reconocidos

    def known(candidate: str) -> bool:
        return any(candidate in other or other in candidate for other in found)

    for code, label, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if not known(match.group()):
                found.append(match.group())
                findings.append(Finding(f"secret:{code}", label))
    for match in _PASSWORD_PHRASE.finditer(_strip_accents(text)):
        value = match.group("value")
        if _looks_like_secret(value) and not known(value):
            found.append(value)
            findings.append(Finding("secret:password", "una contraseña o clave"))
    urls = [match.span() for match in _URL.finditer(text)]
    for start, end in urls:
        for query in _SECRET_QUERY.finditer(text, start, end):
            value = query.group("value")
            has_both = any(c.isalpha() for c in value) and any(c.isdigit() for c in value)
            if has_both and not known(value):
                found.append(value)
                findings.append(Finding("secret:token", "un token o clave con aspecto aleatorio"))
    for match in _TOKEN.finditer(text):
        token, (start, end) = match.group(), match.span()
        if (
            any(url_start <= start and end <= url_end for url_start, url_end in urls)
            or token.startswith("/")  # ruta absoluta
            or _FINGERPRINT.search(text, max(0, start - 8), start)
        ):
            continue
        if _random_token(token) and not known(token):
            found.append(token)
            findings.append(Finding("secret:token", "un token o clave con aspecto aleatorio"))
    return _unique(findings)


# --- caracteres invisibles

_TAGS = re.compile("[\U000e0000-\U000e00ff\U000e01f0-\U000e0fff]")
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
# Un selector de variación legítimo va solo, detrás de un emoji, un signo o un ideograma. En fila
# o detrás de una letra, sirven para esconder un texto entero, a un byte por carácter. FE0F, el de
# los emojis, sobra o se repite a veces al copiar, y al ser uno solo no puede llevar un texto.
_VARIATION = re.compile(
    "(?!\ufe0f\ufe0f)[\ufe00-\ufe0f\U000e0100-\U000e01ef]{2}"
    "|(?<=[\x00-\u024f])(?<![0-9#*\u00a9\u00ae])[\ufe00-\ufe0e]"
    "|(?<![\u2e80-\u2fdf\u3005\u3007\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003ffff])"
    "[\U000e0100-\U000e01ef]"
)
# Escape: con él empiezan las órdenes que el terminal obedece (mover el cursor, borrar o esconder
# lo ya escrito, cambiar el portapapeles). Se rechaza.
_TERMINAL = re.compile("\x1b")
# Los demás de control, salvo el salto de línea y el tabulador, se guardan marcados: aparecen al
# copiar de un PDF o en un texto mal decodificado. La CLI nunca los enseña tal cual.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\x0d(?!\x0a)")
_ZERO_WIDTH = re.compile(  # ZWJ y ZWNJ sueltos, no: los usan emojis y escrituras
    "[\u200b\u2060-\u2064\ufeff\u3164\uffa0\u180e\u206a-\u206f\ufff0-\ufffb]"
    "|[\U0001bca0-\U0001bca3\U0001d173-\U0001d17a]"
    "|(?<=[A-Za-z0-9])[\u200e\u200f\u061c](?=[A-Za-z0-9])"
    # Varios seguidos pueden llevar un texto. Corazón en llamas: FE0F y ZWJ, en ese orden.
    f"|(?!\ufe0f\u200d|\ufe0f\ufe0f)[{_IGNORABLE}]{{2}}"
)


def find_hidden(text: str) -> list[Finding]:
    findings = []
    if _TAGS.search(text):
        findings.append(Finding("hidden:tags", "caracteres Unicode «tag», que esconden texto"))
    if _BIDI.search(text):
        findings.append(Finding("hidden:bidi", "controles que cambian el orden en que se lee"))
    if _VARIATION.search(text):
        findings.append(Finding("hidden:variation", "selectores de variación, que esconden texto"))
    if _TERMINAL.search(text):
        findings.append(Finding("hidden:terminal", "códigos que el terminal obedece"))
    elif _CONTROL.search(text):
        findings.append(Finding("hidden:control", "caracteres de control"))
    rest = _VARIATION.sub("", _BIDI.sub("", _TAGS.sub("", text)))  # ya contados
    if _ZERO_WIDTH.search(rest):
        findings.append(Finding("hidden:zero_width", "caracteres de ancho cero"))
    return findings


# --- instrucciones incrustadas
# Se buscan sobre el texto sin caracteres invisibles, sin tildes y en minúsculas.

_AI = r"(?:asistente|agente|modelo|ia|ai|assistant|agent|model|claude|codex|chatgpt|gpt|copilot)"
_DATA = (
    r"(?:memorias?|datos|todo|toda|todas|lista de clientes|historial|conversacion\w*|"
    r"contrasenas|memory|memories|data|everything|all|customer list|history|conversation\w*|"
    r"credentials)"
)
# Cada hueco excluye lo que sigue, y todo tiene un máximo: sin eso, un texto preparado tardaba
# segundos en cada lectura.
_TO_URL_OR_EMAIL = (
    r"(?:[^.\n]{0,40}https?://\S|[^.\n@]{0,60}(?:\.[\w+-]{1,30}){0,3}@[\w-]{1,63}\.\w)"
)

_INSTRUCTION_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "override",
        "pide ignorar instrucciones",
        re.compile(
            r"\b(?:ignora\w*|olvida\w*|descarta\w*|omite|desobedece\w*|salta\w*)\b[^.\n]{0,40}"
            r"\b(?:instrucciones|indicaciones|reglas|normas|ordenes|directrices|el prompt|"
            r"todo lo anterior|lo anterior|lo que (?:te )?diga)\b"
            r"|\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}"
            r"\b(?:instructions?|rules|guidelines|system prompt|prompt|previous|above|prior)\b"
        ),
    ),
    (
        "role",
        "intenta cambiar el papel del agente",
        re.compile(
            r"\b(?:a partir de ahora|desde ahora|ahora)\s{0,5}(?:,\s{0,5})?"
            r"(?:eres|seras|actuas como|actua como|te comportas como|haces de)\b"
            r"|\b(?:finge|haz como si)\s+(?:ser|que eres)\b"
            r"|\byou are now\s+(?:an?|the|my|in)\b|\byou are now [a-z]+,"
            r"|\bfrom now on,? you\b|\bact as (?:an?|the|my)\b"
            r"|\bpretend (?:to be|you are)\b|\broleplay as\b"
        ),
    ),
    (
        "conceal",
        "pide ocultar algo al usuario",
        re.compile(
            r"\bno\s+(?:se lo |le |les |lo )?(?:digas|cuentes|menciones|informes|avises|reveles|"
            r"ensenes)\b[^.\n]{0,30}\busuari[oa]s?\b"
            r"|\b(?:do not|don't|never)\s+(?:tell|inform|mention|reveal|show)\b[^.\n]{0,30}"
            r"\busers?\b"
        ),
    ),
    (
        "dictated_answer",
        "dicta qué responder",
        re.compile(
            r"\b(?:cuando|si|siempre que)\b[^.\n]{0,40}\bpregunt\w*\b[^.\n]{0,60}"
            r"\b(?:di|responde|contesta|dile|diles|afirma)\s+que\b"
            r"|\b(?:when|whenever|if)\b[^.\n]{0,40}\bask\w*\b[^.\n]{0,60}"
            r"\b(?:say|reply|respond|answer|tell (?:them|him|her))\b"
        ),
    ),
    (
        "tools",
        "pide usar herramientas de la memoria",
        re.compile(
            r"\b(?:llama|usa|ejecuta|invoca|call|use|run|invoke)\b[^.\n]{0,25}"
            r"\b(?:forget|supersede|remember|recall|list_memories)\b"
        ),
    ),
    (
        "exfiltration",
        "pide enviar datos fuera",
        re.compile(
            r"\b(?:envia\w*|manda\w*|sube|reenvia\w*|send|upload|post|forward|exfiltrate)\b"
            rf"[^.\n]{{0,40}}\b{_DATA}\b{_TO_URL_OR_EMAIL}"
        ),
    ),
    (
        "markup",
        "imita marcas de mensajes del sistema",
        re.compile(
            r"</?(?:system|instructions?|assistant|developer)>|\[/?inst\]|<\|im_(?:start|end)\|>"
            r"|(?<!#)#{2,20}\s{0,5}(?:instruccion\w*|instructions?|system|sistema)\b"
        ),
    ),
    (
        "addressed",
        "se dirige al agente como una orden",
        re.compile(
            rf"^\W*{_AI}\s*[,:]"
            rf"|\b(?:nota|mensaje|aviso)(?: interna| interno)? (?:para|al?) (?:el |la )?{_AI}\b"
            rf"|\b(?:el|la) {_AI} (?:debe|tiene que|ha de)\b"
            rf"|\bnote to (?:the )?{_AI}\b|\bthe {_AI} (?:must|has to|should always)\b"
        ),
    ),
    (
        "authority",
        "dice tener prioridad sobre otras fuentes",
        re.compile(
            r"\b(?:esta (?:memoria|nota)|this (?:memory|note))\b[^.\n]{0,40}"
            r"\b(?:unica|prioridad|priority|supersedes|overrides|mas importante|only)\b"
            r"|\b(?:las demas|el resto de las|todas las demas|other|all other) "
            r"(?:memorias|notas|memories|notes)\b[^.\n]{0,40}"
            r"\b(?:no son fiables|desactualizad\w*|falsas|ignor\w*|descart\w*|unreliable|"
            r"outdated|false)"
        ),
    ),
)

_SPACES = re.compile(r"[ \t\u00a0]+")


def visible(text: str) -> str:
    """El texto para enseñarlo en un terminal: lo que el terminal obedecería o no dibujaría sale
    sustituido, y un salto de línea se ve, para que no pueda imitar otra línea de la salida."""
    for hidden in (_CONTROL, _VARIATION, _BIDI, _TAGS):
        text = hidden.sub("\ufffd", text)
    text = re.sub("[\u200e\u200f\u061c]", "", text)  # cambian el orden en que se lee
    text = re.sub("\x0d?\x0a|[\u2028\u2029]", " ⏎ ", text)
    # Muchos espacios seguidos podrían empujar un texto hasta el principio de otra línea.
    return re.sub(r"[\s\u115f\u1160\u3164\uffa0\u2800]{4,}", " ␣ ", text).replace("\t", " ")


def _normalize(text: str) -> str:
    """Sin invisibles, sin tildes, en minúsculas y con un solo espacio entre palabras."""
    return _SPACES.sub(" ", _strip_accents(_INVISIBLE.sub("", text))).lower()


def find_instructions(text: str) -> list[Finding]:
    versions = {_normalize(text)}
    if _BLANK.search(text):  # un relleno puede partir una palabra, o hacer de espacio
        versions.add(_normalize(_BLANK.sub(" ", text)))
    return [
        Finding(f"instruction:{code}", label)
        for code, label, pattern in _INSTRUCTION_PATTERNS
        if any(pattern.search(version) for version in versions)
    ]


# --- uso desde el servicio

_LABELS = {
    finding.code: finding.label
    for finding in (
        *(Finding(f"secret:{code}", label) for code, label, _ in _SECRET_PATTERNS),
        Finding("secret:password", "una contraseña o clave"),
        Finding("secret:token", "un token o clave con aspecto aleatorio"),
        Finding("hidden:tags", "caracteres Unicode «tag», que esconden texto"),
        Finding("hidden:bidi", "controles que cambian el orden en que se lee"),
        Finding("hidden:variation", "selectores de variación, que esconden texto"),
        Finding("hidden:terminal", "códigos que el terminal obedece"),
        Finding("hidden:control", "caracteres de control"),
        Finding("hidden:zero_width", "caracteres de ancho cero"),
        *(Finding(f"instruction:{code}", label) for code, label, _ in _INSTRUCTION_PATTERNS),
    )
}
_BLOCKING_HIDDEN = {"hidden:tags", "hidden:bidi", "hidden:variation", "hidden:terminal"}


def describe(code: str) -> str:
    """El motivo de una marca, en el idioma de quien lee."""
    return tr(_LABELS[code]) if code in _LABELS else code


def labels() -> list[str]:
    """Todas las etiquetas, en español: el test de idiomas comprueba que tienen traducción."""
    return list(_LABELS.values())


def _enumerate(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + tr(" y ") + items[-1]


def _unique(findings: Iterable[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for finding in findings:
        seen.setdefault(finding.code, finding)
    return list(seen.values())


def _present(texts: Iterable[str | None]) -> list[str]:
    return [text for text in texts if text]


def check_before_saving(texts: Iterable[str | None], *, allow_secrets: bool = False) -> None:
    """Rechaza lo que no debe entrar. No repite el texto en el mensaje: puede ser el secreto."""
    present = _present(texts)
    hidden = _unique(f for text in present for f in find_hidden(text) if f.code in _BLOCKING_HIDDEN)
    if hidden:
        raise HiddenCharactersError(
            tr(
                "No se guardó: el texto contiene {what}, que pueden esconder instrucciones. "
                "Quítalos y vuelve a intentarlo.",
                what=_enumerate([describe(f.code) for f in hidden]),
            )
        )
    if allow_secrets:
        return
    secrets = _unique(f for text in present for f in find_secrets(text))
    if secrets:
        raise SecretDetectedError(
            tr(
                "No se guardó: parece contener {what}. Guarda dónde está el secreto (por ejemplo, "
                "«la clave de Stripe está en el gestor de contraseñas»), no el secreto.",
                what=_enumerate([describe(f.code) for f in secrets]),
            )
        )


def flags(texts: Iterable[str | None]) -> list[str]:
    """Marcas de una memoria ya guardada: posibles órdenes, ancho cero o secretos antiguos."""
    present = _present(texts)
    found = _unique(
        finding
        for text in present
        for finding in (*find_instructions(text), *find_hidden(text), *find_secrets(text))
    )
    return [finding.code for finding in found]
