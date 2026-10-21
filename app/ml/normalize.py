"""Clean messy Hinglish/English text so the model sees one spelling per word."""

import re

# Many spellings of the same Hinglish word -> one form.
SPELLINGS = {
    "nai": "nahi", "nhi": "nahi", "nahin": "nahi", "nahi.": "nahi", "ni": "nahi",
    "rha": "raha", "rhe": "rahe", "rhi": "rahi", "rahe": "rahe",
    "hai.": "hai", "h": "hai", "he": "hai", "hain": "hai", "hy": "hai",
    "hu": "hoon", "hun": "hoon", "hoo": "hoon",
    "mai": "main", "mai.": "main", "mei": "mein", "mein": "mein",
    "muje": "mujhe", "mujhe": "mujhe", "mjhe": "mujhe", "mujhko": "mujhe",
    "acha": "accha", "achha": "accha", "acchha": "accha", "aacha": "accha",
    "kaise": "kaise", "kese": "kaise", "kaisey": "kaise", "kase": "kaise",
    "kya": "kya", "kia": "kya", "kyaa": "kya",
    "chahiye": "chahiye", "chahie": "chahiye", "chaiye": "chahiye", "chaheye": "chahiye", "chiye": "chahiye",
    "krna": "karna", "krne": "karne", "kr": "kar", "krta": "karta", "krte": "karte",
    "achchha": "accha", "achchhaa": "accha", "nahin": "nahi", "nahim": "nahi", "bijanes": "business",
    "biznes": "business", "bizanes": "business", "vebasait": "website", "vebsait": "website", "kastamar": "customer",
    "grahak": "customer", "naukari": "job", "dukan": "dukaan", "gadi": "gadi", "gaadi": "gadi",
    "gugal": "google", "gugl": "google", "chek": "check", "listig": "listing", "rivyu": "review",
    "riviu": "review", "rivyuj": "reviews", "vhatsap": "whatsapp", "vhatsaip": "whatsapp", "ophar": "offer",
    # tech words as Hindi voice typing writes them
    "piechapi": "php", "pieychpi": "php", "maiesakyuel": "mysql", "maiesakyooel": "mysql", "esakyuel": "sql",
    "vardapres": "wordpress", "vardpres": "wordpress", "payathan": "python", "paythan": "python", "epiaai": "api",
    "detabes": "database", "detaabes": "database", "kveri": "query", "kvari": "query", "optimaij": "optimize",
    "optimaiz": "optimize", "sophtaveyar": "software", "softaveyar": "software", "devalapar": "developer",
    "devlapar": "developer", "laingvej": "language", "vebasaait": "website", "sarvar": "server", "sarver": "server",
    "aip": "app", "aiplikeshan": "application", "laravel": "laravel", "riekt": "react", "jaavaskript": "javascript",
    "men": "mein", "karata": "karta", "karate": "karte",
    "bhot": "bahut", "bohot": "bahut", "bahot": "bahut", "bht": "bahut",
    "dukan": "dukaan", "dukkan": "dukaan", "shop": "shop",
    "grahak": "customer", "customers": "customer", "costumer": "customer", "custmer": "customer",
    "coustomer": "customer",
    "website": "website", "webiste": "website", "wesite": "website", "websit": "website", "site": "website",
    "wapp": "whatsapp", "watsapp": "whatsapp", "whatsap": "whatsapp", "wa": "whatsapp",
    "msg": "message", "msgs": "message", "messages": "message", "sandesh": "message",
    "bikri": "sales", "sale": "sales", "selling": "sales",
    "paisa": "paise", "paise": "paise", "payment": "payment", "payments": "payment",
    "plz": "please", "pls": "please",
    "u": "you", "ur": "your", "r": "are",
    "gaadi": "gadi", "gaddi": "gadi",
    "naukri": "job", "jobs": "job",
    "t": "to", "toh": "to",
}

HINGLISH_MARKERS = {
    "hai", "nahi", "mera", "meri", "mere", "kya", "ka", "ki", "ko", "mein", "aur", "kaise", "chahiye",
    "raha", "rahe", "rahi", "kar", "karna", "hoon", "bhi", "to", "main", "mujhe", "accha", "bahut",
    "kuch", "koi", "kab", "kyun", "dukaan", "hum", "humara", "apna", "abhi", "wala", "wali", "lagta",
    "chal", "aate", "aata", "jata", "jaye", "jae", "dono", "batao", "bata", "karo", "milta", "milega",
    "mil", "gadi", "pata", "sab", "yaha", "waha", "kaam",
}

# Devanagari -> Hinglish letters, so spoken Hindi (which voice typing writes in Hindi script)
# reaches the model in the same form as typed Hinglish.
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "rh", "फ़": "f",
}
_VOWEL_SIGNS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
                "ृ": "ri", "ॅ": "e", "ॉ": "o"}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
           "ऋ": "ri", "ऑ": "o"}
_NUKTA, _VIRAMA, _MARKS = "\u093c", "\u094d", {"ं": "n", "ँ": "n", "ः": "h"}


def transliterate(text: str) -> str:
    out: list[str] = []
    chars = list(text.replace("\u200d", "").replace("\u200c", ""))
    i = 0
    while i < len(chars):
        ch = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == _NUKTA:
            ch += _NUKTA
            i += 1
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in _CONSONANTS:
            out.append(_CONSONANTS[ch])
            if nxt in _VOWEL_SIGNS:
                out.append(_VOWEL_SIGNS[nxt])
                i += 1
            elif nxt == _VIRAMA:
                i += 1
            elif nxt and (nxt in _CONSONANTS or nxt in _MARKS):
                out.append("a")      # inherent vowel inside a word
            # at a word end the inherent "a" is silent in Hindi: chal, not chala
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in _MARKS:
            out.append(_MARKS[ch])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_TOKEN = re.compile(r"[a-z0-9ऀ-ॿ]+(?:[.'][a-z0-9]+)*")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_REPEATS = re.compile(r"([a-z])\1{2,}")  # letters only, never digits


def tokens(text: str) -> list[str]:
    if _DEVANAGARI.search(text):
        text = transliterate(text)
    text = _REPEATS.sub(r"\1\1", text.lower())  # "plzzzz" -> "plzz"
    return [SPELLINGS.get(t, t) for t in _TOKEN.findall(text)]


def normalize(text: str) -> str:
    return " ".join(tokens(text))


def detect_language(text: str) -> str:
    if _DEVANAGARI.search(text):
        return "Hindi"
    words = tokens(text)
    if not words:
        return "English"
    hits = sum(1 for w in words if w in HINGLISH_MARKERS)
    if hits >= 2 or (hits == 1 and len(words) <= 4):
        return "Hinglish"
    return "English"
