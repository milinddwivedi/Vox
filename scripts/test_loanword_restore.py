import re
import sys
from typing import List, Dict

DEVA_TO_ENGLISH_LOANWORDS = [
    # Multi-word tech/business phrases first (longest first)
    (r"\bमाइक्रोसॉफ्ट\s+टीम्स\b", "Microsoft Teams"),
    (r"\bगूगल\s+मीट\b", "Google Meet"),
    (r"\bस्क्रीन\s+शेयर\b", "screen share"),
    (r"\bबग\s+फिक्स\b", "bug fix"),
    (r"\bकोड\s+रिव्यू\b", "code review"),
    (r"\bप्रोजेक्ट\s+अपडेट\b", "project update"),
    (r"\bएपीआई\s+एंडपॉइंट\b", "API endpoint"),
    (r"\bपीआर\s+मर्ज\b", "PR merge"),
    (r"\bथैंक\s+यू\b", "thank you"),

    # Single words
    (r"\bमाइक्रोसॉफ्ट\b", "Microsoft"),
    (r"\bटीम्स\b", "Teams"),
    (r"\bगूगल\b", "Google"),
    (r"\bज़ूम\b|\bजूम\b", "Zoom"),
    (r"\bस्लैक\b", "Slack"),
    (r"\bजिरा\b|\bजीरा\b", "Jira"),
    (r"\bआउटलुक\b", "Outlook"),
    (r"\bविंडोज\b", "Windows"),
    (r"\bपायथन\b", "Python"),
    (r"\bएसक्यूएल\b", "SQL"),
    (r"\bएसक्यूलाइट\b", "SQLite"),
    (r"\bएपीआई\b", "API"),
    (r"\bएंडपॉइंट\b", "endpoint"),
    (r"\bफ्रंटएंड\b", "frontend"),
    (r"\bबैकएंड\b", "backend"),
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
    (r"\bडेटाबेस\b", "database"),
    (r"\bयूजर\b", "user"),
    (r"\bयूज़र्स\b|\bयूजर्स\b", "users"),
    (r"\bलॉगिन\b|\bलॉगइन\b", "login"),
    (r"\bपासवर्ड\b", "password"),
    (r"\bस्क्रीनशॉट\b", "screenshot"),
    (r"\bनोटिफिकेशन\b", "notification"),
    (r"\bरिकॉर्डिंग\b", "recording"),
    (r"\bट्रांसक्रिप्ट\b", "transcript"),
    (r"\bप्रेजेंटेशन\b", "presentation"),
    (r"\bस्लाइड्स\b|\bस्लाइड\b", "slides"),
    (r"\bओके\b", "OK"),
    (r"\bहाय\b", "Hi"),
    (r"\bहेलो\b|\bहेल्लो\b", "Hello"),
    (r"\bथैंक्स\b", "thanks"),
    (r"\bसॉरी\b", "sorry"),
    (r"\bबाय\b", "bye"),
]

def restore_loanwords(text: str) -> str:
    out = text
    for pat, repl in DEVA_TO_ENGLISH_LOANWORDS:
        out = re.sub(pat, repl, out)
    return out

tests = [
    "मैं स्क्रीन शेयर कर रहा हूँ, क्या आप देख सकते हैं?",
    "कल सुबह नौ बजे कनेक्ट करते हैं.",
    "यह बग फिक्स हो गया है, अब पीआर मर्ज कर दो.",
    "कृपया इस रिपोर्ट को चेक कर लीजिए.",
    "मुझे लगता है कि हमें टीम्स पे बात करनी चाहिए.",
    "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
    "एपीआई एंडपॉइंट डिप्लॉय हो गया है, अब फ्रंटएंड में बग फिक्स करना पड़ेगा।",
]

print("=== Restored English Loanwords in Bilingual Mode ===")
for t in tests:
    print(restore_loanwords(t))
