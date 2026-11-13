# -*- coding: utf-8 -*-
import sys
import re
from typing import List, Dict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEVA_TO_ENGLISH_LOANWORDS = [
    # Multi-word tech/business phrases first (longest first)
    (r"\bमाइक्रोसॉफ्ट\s+टीम्स\b", "Microsoft Teams"),
    (r"\bगूगल\s+मीट\b", "Google Meet"),
    (r"\bस्क्रीन\s+शेयर\b", "screen share"),
    (r"\bस्क्रीन\s+शॉट\b|\bस्क्रीनशॉट\b", "screenshot"),
    (r"\bबग\s+फिक्स\b", "bug fix"),
    (r"\bकोड\s+रिव्यू\b", "code review"),
    (r"\bप्रोजेक्ट\s+अपडेट\b", "project update"),
    (r"\bएपीआई\s+एंडपॉइंट\b", "API endpoint"),
    (r"\bपीआर\s+मर्ज\b", "PR merge"),
    (r"\bथैंक\s+यू\b", "thank you"),
    (r"\bवॉइस\s+टाइपिंग\b|\bवाइस\s+टाइपिंग\b", "voice typing"),

    # Single words - Tech & Tools
    (r"\bमाइक्रोसॉफ्ट\b", "Microsoft"),
    (r"\bटीम्स\b", "Teams"),
    (r"\bगूगल\b", "Google"),
    (r"\bज़ूम\b|\bजूम\b", "Zoom"),
    (r"\bस्लैक\b", "Slack"),
    (r"\bजिरा\b|\bजीरा\b", "Jira"),
    (r"\bगिटहब\b|\bगिट\b", "GitHub"),
    (r"\bआउटलुक\b", "Outlook"),
    (r"\bविंडोज\b", "Windows"),
    (r"\bपायथन\b", "Python"),
    (r"\bएसक्यूएल\b", "SQL"),
    (r"\bएसक्यूलाइट\b", "SQLite"),
    (r"\bएपीआई\b", "API"),
    (r"\bएंडपॉइंट\b", "endpoint"),
    (r"\bफ्रंटएंड\b|\bफ्रंट\s+एंड\b", "frontend"),
    (r"\bबैकएंड\b|\bबैक\s+एंड\b", "backend"),
    (r"\bडिप्लॉय\b", "deploy"),
    (r"\bडिप्लॉयमेंट\b", "deployment"),
    (r"\bप्रोडक्शन\b", "production"),
    (r"\bस्टेजिंग\b", "staging"),
    (r"\bकमिट\b", "commit"),
    (r"\bमर्ज\b", "merge"),
    (r"\bपीआर\b", "PR"),
    (r"\bब्रांच\b", "branch"),
    (r"\bक्लाइंट\b", "client"),
    (r"\bमीटिंग\b", "meeting"),
    (r"\bप्रोजेक्ट\b", "project"),
    (r"\bअपडेट\b", "update"),
    (r"\bस्क्रीन\b", "screen"),
    (r"\bशेयर\b", "share"),
    (r"\bकनेक्ट\b", "connect"),
    (r"\bचेक\b", "check"),
    (r"\bफिक्स\b", "fix"),
    (r"\bबग\b", "bug"),
    (r"\bरिपोर्ट\b", "report"),
    (r"\bऑफिस\b", "office"),
    (r"\bप्लीज\b|\bप्लिज\b|\bप्लीज़\b", "please"),
    (r"\bकॉल\b", "call"),
    (r"\bलिंक\b", "link"),
    (r"\bमैसेज\b", "message"),
    (r"\bचैट\b", "chat"),
    (r"\bसिंक\b", "sync"),
    (r"\bटास्क\b", "task"),
    (r"\bटिकट\b", "ticket"),
    (r"\bईमेल\b|\bमेल\b", "email"),
    (r"\bफाइल\b|\bफ़ाइल\b", "file"),
    (r"\bफाइल्स\b|\bफ़ाइल्स\b", "files"),
    (r"\bफोल्डर\b|\bफ़ोल्डर\b", "folder"),
    (r"\bडॉक्यूमेंट\b|\bडॉक\b", "document"),
    (r"\bसर्वर\b", "server"),
    (r"\bक्लाउड\b", "cloud"),
    (r"\bसॉफ्टवेयर\b", "software"),
    (r"\bहार्डवेयर\b", "hardware"),
    (r"\bडेटाबेस\b", "database"),
    (r"\bडेटा\b", "data"),
    (r"\bयूजर\b", "user"),
    (r"\bयूज़र्स\b|\bयूजर्स\b", "users"),
    (r"\bलॉगिन\b|\bलॉगइन\b", "login"),
    (r"\bपासवर्ड\b", "password"),
    (r"\bनोटिफिकेशन\b", "notification"),
    (r"\bरिकॉर्डिंग\b", "recording"),
    (r"\bट्रांसक्रिप्ट\b", "transcript"),
    (r"\bप्रेजेंटेशन\b", "presentation"),
    (r"\bस्लाइड्स\b|\bस्लाइड\b", "slides"),
    (r"\bटाइप\b", "type"),
    (r"\bटाइपिंग\b", "typing"),
    (r"\bऑनलाइन\b", "online"),
    (r"\bऑफलाइन\b", "offline"),
    (r"\bइंटरनेट\b", "internet"),
    (r"\bनेटवर्क\b", "network"),
    (r"\bब्राउज़र\b|\bब्राउजर\b", "browser"),
    (r"\bसिस्टम\b", "system"),
    (r"\bसर्विस\b", "service"),
    (r"\bस्टेटस\b", "status"),
    (r"\bरिलीज\b", "release"),
    (r"\bटेस्ट\b", "test"),
    (r"\bटेस्टिंग\b", "testing"),
    (r"\bकोड\b", "code"),
    (r"\bकोडिंग\b", "coding"),
    (r"\bवेबसाइट\b", "website"),
    (r"\bऐप\b|\bएप\b", "app"),
    (r"\bऐप्लिकेशन\b|\bएप्लिकेशन\b", "application"),
    (r"\bओके\b", "OK"),
    (r"\bहाय\b", "hi"),
    (r"\bहेलो\b|\bहेल्लो\b", "hello"),
    (r"\bथैंक्स\b", "thanks"),
    (r"\bसॉरी\b", "sorry"),
    (r"\bबाय\b", "bye"),
    (r"\bकंप्लीट\b", "complete"),
]

# High-frequency conversational Hinglish word dictionary
HINGLISH_WORD_MAP = {
    # Pronouns & determiners
    "मैं": "mai", "मै": "mai", "मुझे": "mujhe", "मुझको": "mujhko", "मेरा": "mera", "मेरी": "meri", "मेरे": "mere",
    "हम": "hum", "हमें": "humein", "हमे": "humein", "हमको": "humko", "हमारा": "hamara", "हमारी": "hamari", "हमारे": "hamare",
    "आप": "aap", "आपको": "aapko", "आपका": "aapka", "आपकी": "aapki", "आपके": "aapke",
    "तुम": "tum", "तुम्हें": "tumhein", "तुम्हे": "tumhein", "तुमको": "tumko", "तुम्हारा": "tumhara", "तुम्हारी": "tumhari", "तुम्हारे": "tumhare",
    "तू": "tu", "तुझे": "tujhe", "तेरा": "tera", "तेरी": "teri", "तेरे": "tere",
    "यह": "yeh", "ये": "ye", "इस": "is", "इसे": "ise", "इसको": "isko", "इसका": "iska", "इसकी": "iski", "इसके": "iske", "इससे": "isse",
    "वह": "woh", "वो": "woh", "उस": "us", "उसे": "use", "उसको": "usko", "उसका": "uska", "उसकी": "uski", "उसके": "uske", "उससे": "usse",
    "वे": "ve", "उन": "un", "उन्हें": "unhein", "उन्हे": "unhein", "उनको": "unko", "उनका": "unka", "उनकी": "unki", "उनके": "unke",
    "इन": "in", "इन्हें": "inhein", "इन्हे": "inhein", "इनको": "inko", "इनका": "inka", "इनकी": "inki", "इनके": "inke",
    "जो": "jo", "जिस": "jis", "जिसे": "jise", "जिसको": "jisko", "जिसका": "jiska", "जिसकी": "jiski", "जिसके": "jiske", "जिससे": "jisse",
    "जिन्हें": "jinhein", "जिनको": "jinko",
    "कोई": "koi", "किसी": "kisi", "किसे": "kise", "किसको": "kisko", "किसका": "kiska", "किसकी": "kiski", "किसके": "kiske",
    "सब": "sab", "सभी": "sabhi", "सबको": "sabko", "सबका": "sabka", "सब कुछ": "sab kuch", "कुछ": "kuch",

    # Conjunctions & Particles
    "और": "aur", "या": "ya", "लेकिन": "lekin", "पर": "par", "किंतु": "kintu", "परंतु": "parantu",
    "मगर": "magar", "क्योंकि": "kyunki", "चूंकि": "chunki", "ताकि": "taaki", "तो": "toh",
    "अगर": "agar", "यदि": "yadi", "जब": "jab", "तब": "tab", "जैसे": "jaise", "वैसे": "waise",
    "भी": "bhi", "ही": "hi", "तक": "tak", "भर": "bhar", "मात्र": "matra", "सिर्फ": "sirf", "केवल": "kewal",
    "कि": "ki", "के": "ke", "की": "ki", "का": "ka", "को": "ko", "में": "mein", "मे": "mein", "से": "se", "पे": "pe",
    "साथ": "saath", "बिना": "bina", "बारे": "baare", "लिए": "liye",

    # Question words
    "क्या": "kya", "क्यों": "kyun", "क्यो": "kyun", "कैसे": "kaise", "कब": "kab",
    "कहाँ": "kahan", "कहां": "kahan", "कहा": "kahan", "किधर": "kidhar", "कितना": "kitna", "कितनी": "kitni", "कितने": "kitne",
    "कौन": "kaun", "किस": "kis",

    # Common adverbs & adjectives
    "हाँ": "haan", "हां": "haan", "नहीं": "nahi", "नही": "nahi", "मत": "mat", "ना": "na",
    "अच्छा": "achha", "अच्छी": "achhi", "अच्छे": "achhe",
    "बुरा": "bura", "बुरी": "buri", "bure": "bure",
    "बहुत": "bahut", "थोड़ा": "thoda", "थोड़ी": "thodi", "थोड़े": "thode", "कम": "kam", "ज्यादा": "zyada", "ज़्यादा": "zyada",
    "सही": "sahi", "गलत": "galat", "ठीक": "theek",
    "आज": "aaj", "कल": "kal", "परसों": "parson", "रोज": "roz", "रोज़": "roz", "हमेशा": "hamesha", "कभी": "kabhi",
    "अभी": "abhi", "बाद": "baad", "पहले": "pehle", "जल्दी": "jaldi", "धीरे": "dheere",
    "यहाँ": "yahan", "यहां": "yahan", "वहाँ": "wahan", "वहां": "wahan", "जहाँ": "jahan", "जहां": "jahan",
    "ज़रूर": "zaroor", "जरूर": "zaroor", "शायद": "shayad", "बिल्कुल": "bilkul",
    "कृपया": "kripya", "धन्यवाद": "dhanyawaad", "नमस्ते": "namaste",

    # Auxiliaries & Copula
    "है": "hai", "हैं": "hain", "हूँ": "hoon", "हूं": "hoon", "हो": "ho",
    "था": "tha", "थी": "thi", "थे": "the",
    "होगा": "hoga", "होगी": "hogi", "होंगे": "honge",
    "चाहिए": "chahiye", "चाहता": "chahta", "चाहती": "chahti", "चाहते": "chahte",
    "सकता": "sakta", "सकती": "sakti", "सकते": "sakte", "सकें": "sakein", "सके": "sake", "सकेंगे": "sakenge",
    "रहा": "raha", "रही": "rahi", "रहे": "rahe",
    "गया": "gaya", "गई": "gayi", "गए": "gaye",
    "हुआ": "hua", "हुई": "hui", "हुए": "hue",

    # Common verbs & imperatives
    "करना": "karna", "करनी": "karni", "करने": "karne", "करता": "karta", "करती": "karti", "करते": "karte", "कर": "kar", "करो": "karo", "करें": "karein", "करेंगें": "karenge",
    "कीजिए": "kijiye", "दीजिए": "dijiye", "लीजिए": "lijiye", "देखिए": "dekhiye", "बोलिए": "boliye", "बताइए": "bataiye", "सुनिए": "suniye",
    "देना": "dena", "देनी": "deni", "देने": "dene", "देता": "deta", "देती": "deti", "देते": "dete", "दे": "de", "दो": "do", "दें": "dein",
    "लेना": "lena", "लेनी": "leni", "लेने": "lene", "लेता": "leta", "लेती": "leti", "लेते": "lete", "ले": "le", "लो": "lo", "लें": "lein",
    "जाना": "jaana", "जानी": "jaani", "जाने": "jaane", "जाता": "jaata", "जाती": "jaati", "जाते": "jaate", "जा": "ja", "जाओ": "jao", "जाएं": "jaayein", "जाए": "jaaye",
    "आना": "aana", "आनी": "aani", "आने": "aane", "आता": "aata", "आती": "aati", "आते": "aate", "आ": "aa", "आओ": "aao", "आएं": "aayein", "आए": "aaye",
    "बोलना": "bolna", "बोलनी": "bolni", "बोलने": "bolne", "बोलता": "bolta", "बोलती": "bolti", "बोलते": "bolte", "बोलो": "bolo", "बोलें": "bolein", "बोलूं": "boloon", "बोलूँ": "boloon",
    "बताना": "bataana", "बताना": "bataana", "बताने": "bataane", "बताता": "bataata", "बताती": "bataati", "बताते": "bataate", "बताओ": "batao", "बताएं": "bataayein",
    "देखना": "dekhna", "देखनी": "dekhni", "देखने": "dekhne", "देखता": "dekhta", "देखती": "dekhti", "देखते": "dekhte", "देखो": "dekho", "देखें": "dekhein",
    "सुनना": "sunna", "सुननी": "sunni", "सुनने": "sunne", "सुनता": "sunta", "सुनती": "sunti", "सुनते": "sunte", "सुनो": "suno", "सुनें": "sunein",
    "समझना": "samajhna", "समझनी": "samajhni", "समझने": "samajhne", "समझता": "samajhta", "समझती": "samajhti", "समझते": "samajhte", "समझ": "samajh",
    "लगता": "lagta", "लगती": "lagti", "लगते": "lagte", "लगेगा": "lagega", "लगेगी": "lagegi", "लगेंगे": "lagenge",
    "पहुंचना": "pahunchna", "पहुंचने": "pahunchne", "पहुंचे": "pahunche", "पहुंच": "pahunch",

    # Nouns
    "काम": "kaam", "बात": "baat", "बातचीत": "baatcheet", "समय": "samay", "वक़्त": "waqt", "वक्त": "waqt",
    "दोस्त": "dost", "लोग": "log", "घर": "ghar", "दिन": "din", "रात": "raat", "सुबह": "subah", "शाम": "shaam",
    "जगह": "jagah", "तरफ": "taraf", "तरह": "tarah", "वजह": "wajah",
    "समस्या": "samasya", "दिक्कत": "dikkat", "समाधान": "samadhan", "मदद": "madad",
    "सवाल": "sawaal", "जवाब": "jawaab", "जानकारी": "jaankari", "खबर": "khabar",
    "नाम": "naam", "चीज": "cheez", "चीज़": "cheez", "बातें": "baatein", "कामों": "kaamon",
    "हिंदी": "Hindi", "हिन्दी": "Hindi", "अंग्रेजी": "English", "अंग्रेज़ी": "English", "भारत": "Bharat", "इंडिया": "India",
}

cons_map = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "d", "ढ़": "rh", "फ़": "f",
}
indep_vowels = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o",
}
matras = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o",
}

def transliterate_word(word: str) -> str:
    if word in HINGLISH_WORD_MAP:
        return HINGLISH_WORD_MAP[word]
    if not any("\u0900" <= c <= "\u097F" for c in word):
        return word

    res: List[str] = []
    n = len(word)
    i = 0
    while i < n:
        ch = word[i]
        # Handle nukta combinations
        if i + 1 < n and word[i + 1] == "़" and (ch + "़") in cons_map:
            c_rom = cons_map[ch + "़"]
            i += 2
        elif ch in cons_map:
            c_rom = cons_map[ch]
            i += 1
        elif ch in indep_vowels:
            res.append(indep_vowels[ch])
            i += 1
            continue
        elif ch in ("ं", "ँ"):
            res.append("n")
            i += 1
            continue
        elif ch == "ः":
            res.append("h")
            i += 1
            continue
        elif ch in matras:
            res.append(matras[ch])
            i += 1
            continue
        elif ch == "्":
            i += 1
            continue
        else:
            res.append(ch)
            i += 1
            continue

        # Consonant followed by virama, matra, or inherent schwa
        if i < n and word[i] == "्":
            res.append(c_rom)
            i += 1
        elif i < n and word[i] in matras:
            m_rom = matras[word[i]]
            # Word-final 'aa' -> 'a' in conversational Hinglish
            if m_rom == "aa" and i == n - 1 and n > 2:
                m_rom = "a"
            # Word-final 'ee' -> 'i' in conversational Hinglish (e.g. karni, meri, aapki)
            elif m_rom == "ee" and i == n - 1 and n > 2:
                m_rom = "i"
            res.append(c_rom + m_rom)
            i += 1
        else:
            # Medial schwa deletion heuristics:
            # In Hindi, medial consonant before suffixes like -te, -ti, -ta, -ne, -na, -ke drops the inherent 'a':
            # e.g., C + (ते, ती, ता, ने, ना, के) -> C without 'a'
            if i < n:
                next_part = word[i:]
                if re.match(r"^[क-ह][ािीुूेैोौ]", next_part) and len(word) >= 3 and i >= 1:
                    # e.g. स + क + ते -> 's' + 'k' (no 'a') + 'te'
                    res.append(c_rom)
                else:
                    res.append(c_rom + "a")
            else:
                # Word-final consonant: silent schwa
                res.append(c_rom)

    out = "".join(res)
    # Post-cleanups
    out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]e\b)", r"\1\2", out)  # e.g. karate -> karte
    out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]a\b)", r"\1\2", out)  # e.g. karata -> karta
    out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]i\b)", r"\1\2", out)  # e.g. karati -> karti
    out = re.sub(r"ee$", "i", out)  # final ee -> i
    return out

def restore_loanwords(text: str) -> str:
    out = text
    for pat, repl in DEVA_TO_ENGLISH_LOANWORDS:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out

def devanagari_to_roman_hinglish(text: str) -> str:
    if not text or not any("\u0900" <= ch <= "\u097F" for ch in text):
        return text

    # Step 1: Restore English loanwords first
    text = restore_loanwords(text)

    # Step 2: Split tokens preserving punctuation
    tokens = re.split(r"([^\w\u0900-\u097F]+)", text)
    converted = [transliterate_word(tok) for tok in tokens]
    res = "".join(converted)
    return res

test_sentences = [
    "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए.",
    "मैं स्क्रीन शेयर कर रहा हूँ, क्या आप देख सकते हैं?",
    "कल सुबह नौ बजे कनेक्ट करते हैं.",
    "यह बग फिक्स हो गया है, अब पीआर मर्ज कर दो.",
    "कृपया इस रिपोर्ट को चेक कर लीजिए.",
    "मुझे लगता है कि हमें टीम्स पे बात करनी चाहिए.",
    "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
    "एपीआई एंडपॉइंट डिप्लॉय हो गया है, अब फ्रंटएंड में बग फिक्स करना पड़ेगा।",
]

print("=== CONVERTED HINGLISH RESULTS ===")
for s in test_sentences:
    print("IN :", s)
    print("OUT:", devanagari_to_roman_hinglish(s))
    print()
