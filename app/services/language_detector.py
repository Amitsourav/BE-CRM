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

    if any(char in HINDI_CHARS for char in text):
        return "hi"

    words = set(text.lower().split())
    hindi_count = len(words & HINDI_WORDS)

    if hindi_count >= 1:
        return "hi"

    return "en"


def get_language_instruction(language: str) -> str:
    if language == "hi":
        return (
            "[LANGUAGE INSTRUCTION: User spoke Hindi/Hinglish. "
            "You MUST reply in Hinglish (natural Hindi+English mix). "
            "Use female Hindi grammar: karungi, sakti hoon, chahti hoon.]"
        )
    return (
        "[LANGUAGE INSTRUCTION: User spoke English. "
        "You MUST reply in PURE ENGLISH ONLY. "
        "Zero Hindi words allowed. Not even: aur, toh, hai, "
        "ke liye, kar rahe ho, kya, achha, bilkul.]"
    )
