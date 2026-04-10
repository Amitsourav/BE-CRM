HINDI_WORDS = {
    # Acknowledgments / yes-no
    "haan", "nahi", "nhi", "ji", "haanji", "nahin", "bilkul",
    "achha", "accha", "theek", "thik", "zaroor", "sahi",
    # Question words
    "kya", "kitna", "kitni", "kitne", "kaisa", "kaisi", "kaise",
    "kahan", "kahaan", "kaun", "kyun", "kyu", "kab",
    # Pronouns / possessives
    "main", "mein", "mujhe", "mujhse", "mera", "meri", "mere",
    "aap", "aapka", "aapki", "aapke", "aapko", "aapse",
    "woh", "wo", "uska", "uski", "uske", "unka", "unki", "unke",
    "yeh", "ye", "iska", "iski", "iske", "inke",
    "hum", "humara", "humari", "humare", "humko", "humse",
    "tera", "teri", "tere", "tumhara", "tumhari", "tumhare",
    # Postpositions / connectors (critical for detection!)
    "ka", "ki", "ke", "ko", "se", "mein", "par", "pe",
    "liye", "ke_liye", "wala", "wali", "wale",
    "aur", "ya", "lekin", "par", "toh", "to",
    "isliye", "kyunki", "phir", "abhi", "tab", "jab",
    # Common verbs / verb forms
    "hai", "hain", "ho", "tha", "thi", "the",
    "hoon", "hoo", "hun", "hoga", "hogi", "hoge",
    "chahiye", "chahta", "chahti", "chahte",
    "karna", "karta", "karti", "karte", "karunga", "karungi",
    "sakta", "sakti", "sakte", "sakti",
    "raha", "rahi", "rahe", "rahe",
    "jana", "jata", "jati", "jate", "jaunga", "jaungi",
    "aana", "aata", "aati", "aate", "aayega", "aayegi",
    "rehna", "rehta", "rehti", "rehte",
    "lena", "leta", "leti", "lete", "lena", "lunga", "lungi",
    "dena", "deta", "deti", "dete", "dunga", "dungi",
    "dekho", "dekh", "dekhna", "dekhte", "dekha",
    "bolo", "bol", "bolta", "bolti", "bolna",
    "batao", "bata", "batana", "bataye",
    "suno", "sun", "sunna", "soch", "socho", "sochna",
    "milega", "milegi", "milenge", "mila", "mili",
    "samjha", "samjhi", "samjhe", "samjho",
    # Time / misc
    "kal", "aaj", "parso", "abhi", "baad", "pehle", "matlab",
    # Money
    "paisa", "paise", "rupee", "rupaye", "lakh", "crore",
    # Common casual speech
    "yaar", "bhai", "didi", "sir", "madam",
    "chalo", "chal", "chalega", "chalegi",
    "pata", "naa", "na", "bas", "bohot", "bahut", "zyada",
    "koi", "kuch", "sab", "apna", "apni", "apne",
    "wapas", "dobara", "pehle", "baad",
}

HINDI_CHARS = set(
    "अआइईउऊएओकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह"
)

# English words that Sarvam STT commonly transliterates into Devanagari
# when locked to hi-IN. If the Devanagari transcript contains enough of
# these, the user was actually speaking English — not Hindi. Without
# this override, 'I am looking for European country' gets transcribed as
# 'मैं लुकिंग फॉर यूरोपीयन कंट्री' and our detector flags it as Hindi,
# triggering an unwanted Hinglish reply.
ENGLISH_IN_DEVANAGARI = {
    # Pronouns / auxiliaries
    "आई", "यू", "वी", "ही", "शी", "इट", "दे", "एम", "इज", "आर",
    "वाज", "वेयर", "बी", "बीन", "बीइंग", "हैव", "हैज", "हैड",
    "डू", "डज", "डिड", "कैन", "कुड", "विल", "वुड", "शुड", "माइट",
    "मे", "मस्ट", "नॉट", "डोंट", "डिडंट", "कांट", "वोंट",
    # Common connectors
    "एंड", "ऑर", "बट", "सो", "इफ", "वेन", "वाइल", "एज", "आफ्टर",
    "बिफोर", "फॉर", "फ्रॉम", "टू", "ऑफ", "इन", "ऑन", "एट", "बाय",
    "विथ", "विदाउट", "अबाउट", "ओवर", "अंडर",
    # Social / small talk / acknowledgments (ambiguous with Hindi)
    "यस", "नो", "ओके", "ओकेय", "हलो", "हेलो", "हाय", "थैंक्स",
    "थैंक", "थैंक्यू", "सॉरी", "प्लीज", "वेलकम", "बाय",
    "गुड", "बैड", "नाइस", "ग्रेट", "राइट", "रांग",
    # Common ambiguous tokens — these can be Hindi but in phone
    # conversations are almost always English fillers from the user
    "ये", "ये।", "सर", "मैम", "मैडम", "ओह", "आह", "हम्म", "ओ",
    "एर", "उम", "आहा", "व्हाट", "हाउ", "व्हेन", "वेयर", "व्हाय",
    "एक्टुअली", "एक्चुअली", "बेसिकली", "रियली", "लिटरली",
    "प्लीज", "श्योर", "ऑफकोर्स", "ऑफ-कोर्स", "सो-सो",
    "माइसेल्फ", "यूअरसेल्फ", "हिमसेल्फ", "हरसेल्फ",
    # Conversation / intent
    "लुकिंग", "सर्चिंग", "वांट", "नीड", "नीडिंग", "प्लान",
    "प्लानिंग", "टेक", "टेकिंग", "गो", "गोइंग", "कम", "कमिंग",
    "स्टार्ट", "स्टार्टिंग", "फिनिश", "एंड",
    # Domain (education loans)
    "लोन", "कोर्स", "कोर्सेज", "कॉलेज", "कॉलेजेज", "यूनिवर्सिटी",
    "यूनिवर्सिटीज", "स्टूडेंट", "स्टडी", "स्टडीज", "स्टडीइंग",
    "एडमिशन", "इंटेक", "डिग्री", "मास्टर्स", "बैचलर्स", "पीएचडी",
    "एमबीए", "एमएस", "एमबीबीएस", "एमटेक", "बीटेक", "फंड", "फंडिंग",
    "कोलेटरल", "को-एप्लीकेंट", "को एप्लीकेंट", "डॉक्यूमेंट",
    "डॉक्यूमेंट्स", "प्रोसेस", "प्रोसेसिंग", "प्रोसेसिंग", "अप्रूवल",
    "अप्रूव्ड", "इंटरेस्ट", "रेट", "रेट्स", "ईएमआई", "मनी",
    "अमाउंट", "सैलरी", "इनकम", "कैश", "बैंक", "लेंडर", "फंडमायकैंपस",
    # Geography
    "कंट्री", "कंट्रीज", "जर्मनी", "कनाडा", "यूएस", "यूएसए", "यूके",
    "अमेरिका", "ऑस्ट्रेलिया", "यूरोप", "यूरोपीयन", "न्यूजीलैंड",
    "आयरलैंड", "सिंगापुर",
    # Time / numbers (English spelled)
    "टुडे", "टुमॉरो", "यस्टरडे", "नेक्स्ट", "लास्ट", "मंथ", "ईयर",
    "वीक", "डे", "ऑवर", "मिनट", "फर्स्ट", "सेकंड", "थर्ड",
    "मिलियन", "बिलियन", "लाख", "करोड़",
}


def _strip_punct(word: str) -> str:
    return word.strip("।.,!?;:\"'()[]{}").strip()


def detect_language(text: str) -> str:
    if not text:
        return "en"

    words = [_strip_punct(w) for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return "en"

    has_devanagari = any(char in HINDI_CHARS for char in text)

    if has_devanagari:
        # Devanagari script is normally a Hindi signal — BUT Sarvam STT
        # transliterates English speech into Devanagari when locked to
        # hi-IN. Check whether the transcript is mostly English words
        # written in Devanagari; if so, treat it as English.
        en_in_dev = sum(1 for w in words if w in ENGLISH_IN_DEVANAGARI)
        if len(words) >= 2 and (en_in_dev / len(words)) >= 0.5:
            return "en"
        # Single-word utterances ("हाँ" / "नहीं" / "ओके") are too short
        # to measure ratio reliably. If the single word itself is in the
        # English-in-Devanagari set, treat it as English, else Hindi.
        if len(words) == 1 and words[0] in ENGLISH_IN_DEVANAGARI:
            return "en"
        return "hi"

    # Romanized Hindi: require ≥2 Hindi tokens AND ≥30% of words.
    # Single-word matches caused English turns to wrongly flip to Hindi
    # (e.g. "I want a loan" → "loan" is in HINDI_WORDS → flipped).
    words_lower = [w.lower() for w in words]
    hindi_count = sum(1 for w in words_lower if w in HINDI_WORDS)
    if hindi_count >= 2 and (hindi_count / len(words_lower)) >= 0.3:
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
