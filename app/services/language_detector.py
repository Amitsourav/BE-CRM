HINDI_WORDS = {
    "haan", "nahi", "nhi", "kya", "kitna",
    "chahiye", "hai", "ho", "aap", "main",
    "achha", "bilkul", "toh", "abhi", "matlab",
    "samjha", "bata", "milega", "theek", "zaroor",
    "kaisa", "kahan", "kaun", "kyun", "kab",
    "kal", "aur", "par", "lekin", "isliye",
    "kyunki", "phir", "woh", "yeh", "iska",
    "uska", "mera", "tera", "humara", "tumhara",
    "batao", "bolo", "suno", "dekho", "lena",
    "dena", "karna", "jana", "aana", "rehna",
    "paisa", "paise", "rupee", "rupaye", "loan",
    "chahta", "chahti", "chahte", "karunga",
    "karungi", "sakta", "sakti", "hoga", "hogi",
}

HINDI_CHARS = set(
    "अआइईउऊएओकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह"
)


def detect_language(text: str) -> str:
    if not text:
        return "en"

    # Devanagari script is a definitive signal
    if any(char in HINDI_CHARS for char in text):
        return "hi"

    # Romanized Hindi: require ≥2 Hindi tokens AND ≥30% of words
    # Single-word matches caused English turns to wrongly flip to Hindi
    # (e.g. "I want a loan" → "loan" is in HINDI_WORDS → flipped)
    words = text.lower().split()
    if not words:
        return "en"
    hindi_count = sum(1 for w in words if w in HINDI_WORDS)
    if hindi_count >= 2 and (hindi_count / len(words)) >= 0.3:
        return "hi"

    return "en"


def get_language_instruction(language: str) -> str:
    if language == "hi":
        return (
            "[LANGUAGE RULES (strict):\n"
            "- User spoke Hindi. Reply in PURE HINDI ONLY.\n"
            "- Do NOT mix Hindi and English in the same sentence.\n"
            "- Mirror the user's language exactly — if they switch to "
            "English on the next turn, you MUST switch back to English.\n"
            "- Never switch languages unless the user explicitly asks.]"
        )
    return (
        "[LANGUAGE RULES (strict):\n"
        "- User spoke English. Reply in PURE ENGLISH ONLY.\n"
        "- Zero Hindi words. Not even: aur, toh, hai, kya, achha, bilkul.\n"
        "- Do NOT mix Hindi and English in the same sentence.\n"
        "- Mirror the user's language exactly — if they switch to Hindi "
        "on the next turn, switch to Hindi; otherwise stay in English.\n"
        "- Never switch languages unless the user explicitly asks.]"
    )
