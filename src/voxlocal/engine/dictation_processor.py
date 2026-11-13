"""
Dictation Text Processor & Custom Vocabulary Engine.
Handles:
- Spoken punctuation commands ("period", "comma", "new line", "new paragraph", "question mark", etc.)
- Dictation cleanup mode (filler word removal like "um", "uh", spacing normalization, auto-capitalization)
- Custom vocabulary replacement (acronyms, names, product codes, technical terms)
- Generating Whisper initial_prompt conditioning from custom vocabulary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional


class TransformationMode(str, Enum):
    """Transformation modes for VOX voice typing (Verbatim, Clean, Polished, Structured, Email)."""
    VERBATIM = "verbatim"
    CLEAN = "clean"
    CLEANED = "cleaned"
    POLISHED = "polished"
    MAGIC = "magic"
    STRUCTURED = "structured"
    EMAIL = "email"


@dataclass
class DictationProcessedResult:
    """Canonical processed text result for Milestone 2 transformation pipeline."""
    raw_text: str
    formatted_text: str
    mode: str
    has_retractions: bool = False
    retracted_segments: List[str] = field(default_factory=list)
    annotated_html: str = ""


class DictationProcessor:
    """Formats and cleans raw speech-to-text output for Universal Voice Typing."""

    # Ordered from longest phrase to shortest to prevent partial matches
    SPOKEN_PUNCTUATION_RULES = [
        (r"\bnew paragraph\b", "\n\n"),
        (r"\bnext paragraph\b", "\n\n"),
        (r"\bnew line\b", "\n"),
        (r"\bnext line\b", "\n"),
        (r"\bquestion mark\b", "?"),
        (r"\bexclamation point\b", "!"),
        (r"\bexclamation mark\b", "!"),
        (r"\bfull stop\b", "."),
        (r"\bopen parenthesis\b", "("),
        (r"\bclose parenthesis\b", ")"),
        (r"\bopen quote\b", '"'),
        (r"\bclose quote\b", '"'),
        (r"\bsemicolon\b", ";"),
        (r"\bperiod\b", "."),
        (r"\bcomma\b", ","),
        (r"\bcolon\b", ":"),
    ]

    # Built-in phonetic & Indian-English accent normalization rules
    ACCENT_CORRECTION_RULES = [
        (r"\bin a cats\b", "in caps"),
        (r"\bin cats\b", "in caps"),
        (r"\ball cats\b", "all caps"),
        (r"\btell you step back\b", "tell you step by step"),
        (r"\bvoice type\b", "voice typing"),
        (r"\b(?:winchel|vinchil|wind\s+chill)\b", "Windchill"),
        (r"\bventure\s+pl[nm]\b", "Windchill PLM"),
        (r"\bsap\s+erpr\b", "SAP ERP"),
        (r"\b(?:p\s*o\s+system|po\s+system)\b", "PO system"),
        (r"\bwhere\s+used\s+view\b", "Where-Used view"),
        (r"\b(?:b\s*o\s*m|b\.o\.m)\b", "BOM"),
        (r"\b(?:double\s*u\s*f\s*x|w\s*f\s*x|w\.f\.x)\b", "WFX"),
        (r"\b(?:p\s*l\s*m|p\.l\.m)\b", "PLM"),
        (r"\b(?:e\s*r\s*p|e\.r\.p)\b", "ERP"),
        (r"\bhub\s*spot\b", "HubSpot"),
        (r"\bsub\s+categor(y|ies)\b", r"subcategor\1"),
        (r"\bbar\s+code(s?)\b", r"barcode\1"),
        (r"\bstand\s+up\s+(?:call|meeting)\b", "standup meeting"),
        (r"\b(?:scrum|kram)\s+call\b", "Scrum call"),
        (r"\b(?:daily|dailyes|dailies)\s+(?:scrum|kram)\b", "Daily Scrum"),
        (r"\bamerican\s+(?:tall|towels?|doll)\b", "American Tall"),
        (r"\btroy\s+le+a?\b", "Troy Lee"),
        (r"\b(?:heman|hemant|hemanth)\s+kumar\b", "Hemanth Kumar"),
        (r"\brum\s*j+h?u+m\b", "Rumjhum"),
        (r"\bprep\s+one\b", "prepone"),
        (r"\bdo the need\s*ful\b", "do the needful"),
        (r"\brevert\s+back\b", "revert"),
        (r"\bloop\s+in\b", "loop in"),
        (r"\btouch\s+base\b", "touch base"),
        (r"\bconnect\s+over\s+teams\b", "connect over Teams"),
        (r"\bconnect\s+over\s+meet\b", "connect over Google Meet"),
        (r"\bp\s*r\s+merge\b", "PR merge"),
        (r"\bp\s*r\s+review\b", "PR review"),
        (r"\b(?:p\s*\.?\s*r|p\.r\.)\b", "PR"),
        (r"\b(?:a\s*\.?\s*p\s*\.?\s*i|a\.p\.i\.)\b", "API"),
        (r"\b(?:s\s*\.?\s*q\s*\.?\s*l|s\.q\.l\.)\b", "SQL"),
        (r"\bv\s*s\s*code\b", "VS Code"),
        (r"\bc\s*i\s*/?\s*c\s*d\b", "CI/CD"),
        # "Hawaii" misheard when user says "how I" (standalone or before verbs/clauses)
        (
            r"\bhawaii\b(?=\s+(?:express|speak|say|talk|do|can|will|should|would|could|want|need|am|was|have|think|feel|work|type|write|use|make|get|see|know)\b)",
            "how I",
        ),
        (r"\b(totally|exactly|that is|this is|like|about)\s+hawaii\b", r"\1 how I"),
        (r"^\s*hawaii[\.\!\?,\s]*$", "how I"),
    ]

    # Words that should remain capitalized even when continuing mid-sentence
    ALWAYS_CAPITALIZE_WORDS = {
        "i",
        "i'm",
        "i'll",
        "i've",
        "i'd",
        "english",
        "hindi",
        "hinglish",
        "india",
        "indian",
        "windows",
        "google",
        "microsoft",
        "zoom",
        "teams",
        "outlook",
        "antigravity",
        "voxlocal",
        "windchill",
        "wfx",
        "plm",
        "bom",
        "po",
        "erp",
        "sap",
        "sql",
        "sqlite",
        "hubspot",
        "jira",
        "github",
        "scrum",
        "hemanth",
        "talathoti",
        "astha",
        "dhiman",
        "riya",
        "kohli",
        "rhythm",
        "walia",
        "rumjhum",
        "pankaj",
        "vineet",
        "manoj",
        "sharma",
        "abhilash",
        "mishra",
        "amar",
        "vohra",
        "yamini",
        "pancholi",
        "anannya",
        "vinod",
        "milind",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }

    # Hanging prepositions/conjunctions where Whisper accidentally drops a period on a mid-sentence pause
    HANGING_CONNECTOR_END_RE = re.compile(
        r"\b(?:and|or|but|because|although|though|while|whereas|if|unless|until|since|so|"
        r"to|for|with|without|in|into|from|of|by|about|between|through|during|before|after|"
        r"as|like|than|that|which|who|whom|whose|where|when|how\s+i|"
        r"और|या|कि|क्योंकि|लेकिन|पर|तो|अगर|जब)\s*[.।]\s*$",
        re.IGNORECASE,
    )

    # Short clause ending in an auxiliary/modal verb + period (e.g. "It can be English, it can.")
    HANGING_MODAL_END_RE = re.compile(
        r"\b(?:can|will|should|would|could|must|may|might|shall|is|are|was|were|am|"
        r"have|has|had|do|does|did)\.\s*$",
        re.IGNORECASE,
    )

    # Orthographic & loanword fixes for Whisper Devanagari outputs
    DEVA_ORTHOGRAPHIC_FIXES = [
        ("टाएप", "टाइप"),
        ("टाईप", "टाइप"),
        ("ताएप", "टाइप"),
        ("चाहीए", "चाहिए"),
        ("चाहीये", "चाहिए"),
        ("चाही", "चाहिए"),
        ("अफिज", "ऑफिस"),
        ("अफिस", "ऑफिस"),
        ("आफिस", "ऑफिस"),
        ("ओफिस", "ऑफिस"),
        ("ख्लाइंट", "क्लाइंट"),
        ("ख्लाइट", "क्लाइंट"),
        ("क्लाईंट", "क्लाइंट"),
        ("कलाइंट", "क्लाइंट"),
        ("प्रोजिक्ट", "प्रोजेक्ट"),
        ("अप्टेट", "अपडेट"),
        ("अप्डेट", "अपडेट"),
        ("इस्रिपोत", "इस रिपोर्ट"),
        ("इस रिपोत", "इस रिपोर्ट"),
        ("रिपोत", "रिपोर्ट"),
        ("रिपोट", "रिपोर्ट"),
        ("कम्प्लीट", "कंप्लीट"),
        ("कमप्लीट", "कंप्लीट"),
        ("अच्ये", "अच्छे"),
        ("आछे", "अच्छे"),
        ("तोजाना", "हो जाना"),
        ("हिन्दी", "हिंदी"),
        ("मुजे", "मुझे"),
        ("खल सुबहा", "कल सुबह"),
        ("कल सुबहा", "कल सुबह"),
        ("कर सुबा", "कल सुबह"),
        ("सुबहा", "सुबह"),
        ("सुभा", "सुबह"),
        ("सुभह", "सुबह"),
        ("सुभ", "सुबह"),
        ("कुप्या", "कृपया"),
        ("क्रुप्या", "कृपया"),
        ("कुरुपया", "कृपया"),
        ("रिशेडियूल", "रिशेड्यूल"),
        ("रीशे दूल", "रिशेड्यूल"),
        ("रीशेडुल", "रिशेड्यूल"),
        ("पहुचने", "पहुंचने"),
        ("थोरा", "थोड़ा"),
        ("तोरा", "थोड़ा"),
        ("ठोड़ा", "थोड़ा"),
        ("पड़ीगा", "पड़ेगा"),
        ("पडीगा", "पड़ेगा"),
        ("पडेगा", "पड़ेगा"),
        ("बोल ना", "बोलना"),
        ("कर ना", "करना"),
        ("देख ना", "देखना"),
        ("सुन ना", "सुनना"),
    ]

    # Comprehensive Devanagari English Loanword Restoration Table (longest multi-word phrases first)
    DEVA_TO_ENGLISH_LOANWORDS = [
        # Multi-word tech, corporate & productivity phrases
        (r"\bमाइक्रोसॉफ्ट\s+टीम्स\b", "Microsoft Teams"),
        (r"\bगूगल\s+मीट\b", "Google Meet"),
        (r"\bस्क्रीन\s+शेयर\b", "screen share"),
        (r"\bस्क्रीन\s+शॉट\b|\bस्क्रीनशॉट\b", "screenshot"),
        (r"\bबग\s+फिक्स\b", "bug fix"),
        (r"\bकोड\s+रिव्यू\b", "code review"),
        (r"\bप्रोजेक्ट\s+अपडेट\b", "project update"),
        (r"\bएपीआई\s+एंडपॉइंट\b", "API endpoint"),
        (r"\bपीआर\s+मर्ज\b", "PR merge"),
        (r"\bपीआर\s+रिव्यू\b", "PR review"),
        (r"\bथैंक\s+यू\b", "thank you"),
        (r"\bवॉइस\s+टाइपिंग\b|\bवाइस\s+टाइपिंग\b", "voice typing"),
        (r"\bपरचेज\s+आर्डर\b|\bपरचेज\s+ऑर्डर\b|\bपर्चेज\s+ऑर्डर\b", "purchase order"),
        (r"\bपरचेज\b|\bपर्चेज\b", "purchase"),
        (r"\bआर्डर\b|\bऑर्डर\b", "order"),
        (r"\bएजेंडा\b", "agenda"),
        (r"\bकास्टिंग\b|\bकॉस्टिंग\b", "costing"),
        (r"\bसप्लायर\b", "supplier"),
        (r"\bशिपमेंट\b", "shipment"),
        (r"\bप्रोसीड\b", "proceed"),
        (r"\bस्टेप\b", "step"),

        # Single words - Tech platforms & developer tools
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
        (r"\bरिशेड्यूल\b|\bरिशेड्युल\b|\bरीशेड्यूल\b", "reschedule"),
        (r"\bशेड्यूल\b|\bशेड्युल\b", "schedule"),
        (r"\bडिस्कस\b", "discuss"),
        (r"\bशेयरिंग\b", "sharing"),
        (r"\bप्रेजेंट\b", "present"),
        (r"\bप्लान\b", "plan"),
        (r"\bऑप्शन\b", "option"),
        (r"\bइश्यू\b|\bइशू\b", "issue"),
        (r"\bप्रॉब्लम\b", "problem"),
        (r"\bसॉल्यूशन\b|\bसॉल्युशन\b", "solution"),
        (r"\bकन्फर्म\b", "confirm"),
        (r"\bवेरिफाई\b", "verify"),
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
        (r"\bसबमिट\b", "submit"),
        (r"\bसबमिटेड\b", "submitted"),
        (r"\bकम्युनिकेट\b", "communicate"),
        (r"\bकम्युनिकेटेड\b", "communicated"),
        (r"\bरिस्पॉन्ड\b|\bरिस्पांड\b", "respond"),
        (r"\bरिस्पॉन्स\b|\bरिस्पांस\b", "response"),
        (r"\bकस्टमर\b", "customer"),
        (r"\bकस्टमर्स\b", "customers"),
        (r"\bवेट\b", "wait"),
        (r"\bअप्रूव\b", "approve"),
        (r"\bअप्रूवल\b", "approval"),
        (r"\bरिजेक्ट\b", "reject"),
        (r"\bपेंडिंग\b", "pending"),
        (r"\bप्रोसेस\b", "process"),
        (r"\bप्रायोरिटी\b", "priority"),
        (r"\bडेडलाइन\b", "deadline"),
        (r"\bटाइमलाइन\b", "timeline"),
        (r"\bरिक्वायरमेंट\b", "requirement"),
        (r"\bरिक्वायरमेंट्स\b", "requirements"),
        (r"\bफीचर\b", "feature"),
        (r"\bफीचर्स\b", "features"),
        (r"\bबिल्ड\b", "build"),
        (r"\bपाइपलाइन\b", "pipeline"),
        (r"\bलॉग्स\b|\bलॉग\b", "logs"),
        (r"\bएरर\b", "error"),
        (r"\bक्रैश\b", "crash"),
        (r"\bपरफॉर्मेंस\b", "performance"),
        (r"\bलेटेन्सी\b|\bलेटेंसी\b", "latency"),
        (r"\bमेमोरी\b", "memory"),
        (r"\bथ्रेड\b", "thread"),
        (r"\bमॉडल\b", "model"),
        (r"\bस्पीकर\b", "speaker"),
        (r"\bऑडियो\b", "audio"),
        (r"\bवीडियो\b", "video"),
        (r"\bडेस्कटॉप\b", "desktop"),
        (r"\bविंडो\b", "window"),
        (r"\bबटन\b", "button"),
        (r"\bपेज\b", "page"),
        (r"\bटेबल\b", "table"),
        (r"\bलिस्ट\b", "list"),
        (r"\bसर्च\b", "search"),
        (r"\bफिल्टर\b", "filter"),
        (r"\bसेटिंग्स\b", "settings"),
        (r"\bवर्जन\b", "version"),
        (r"\bअपग्रेड\b", "upgrade"),
        (r"\bबैकअप\b", "backup"),
        (r"\bसिक्योरिटी\b", "security"),
        (r"\bएक्सेस\b", "access"),
        (r"\bपरमिशन\b", "permission"),
        (r"\bटोकन\b", "token"),
        (r"\bसेशन\b", "session"),
        (r"\bरिक्वेस्ट\b", "request"),
        (r"\bपेमेंट\b", "payment"),
        (r"\bइनवॉइस\b", "invoice"),
        (r"\bबजट\b", "budget"),
        (r"\bप्राइसिंग\b", "pricing"),
        (r"\bवेंडर\b", "vendor"),
        (r"\bमॉड्यूल\b", "module"),
        (r"\bकंपोनेंट\b", "component"),
        (r"\bइंटरफेस\b", "interface"),
        (r"\bडिजाइन\b|\bडिज़ाइन\b", "design"),
        (r"\bरिव्यू\b", "review"),
        (r"\bफीडबैक\b", "feedback"),
        (r"\bडेमो\b", "demo"),
        (r"\bस्प्रिंट\b", "sprint"),
        (r"\bस्टैंडअप\b", "standup"),
        (r"\bस्क्रम\b", "scrum"),
        (r"\bरोडमैप\b", "roadmap"),
        (r"\bडिलीवरी\b", "delivery"),
        (r"\bप्रोग्रेस\b", "progress"),
        (r"\bब्लॉकर\b", "blocker"),
        (r"\bसबकैटेगरी\b|\bसब\s+कैटेगरी\b", "subcategory"),
        (r"\bकैटेगरी\b", "category"),
        (r"\bबारकोड\b|\bबार\s+कोड\b", "barcode"),
        (r"\bहबस्पॉट\b|\bहब\s+स्पॉट\b", "HubSpot"),
        (r"\bडब्ल्यूएफएक्स\b", "WFX"),
        (r"\bपीएलएम\b", "PLM"),
        (r"\bईआरपी\b", "ERP"),
        (r"\bएक्सपोर्ट\b", "export"),
        (r"\bइंपोर्ट\b|\bइम्पोर्ट\b", "import"),
        (r"\bकॉलम\b", "column"),
        (r"\bशीट\b", "sheet"),
        (r"\bएक्सेल\b", "Excel"),
        (r"\bक्लाइंट्स\b", "clients"),
        (r"\bरिजॉल्व\b|\bरिज़ॉल्व\b", "resolve"),
        (r"\bरिजॉल्व्ड\b|\bरिज़ॉल्व्ड\b", "resolved"),
        (r"\bइंप्लीमेंट\b|\bइम्प्लीमेंट\b", "implement"),
        (r"\bकॉन्फ़िगरेशन\b|\bकॉन्फिगरेशन\b", "configuration"),
    ]

    # Conversational dictionary + phonetic mapping for Romanized Hinglish ('hi_latn') mode
    HINGLISH_WORD_MAP = {
        # Pronouns & determiners (including ergative -ne forms)
        "मैं": "mai", "मै": "mai", "मैंने": "maine", "मैने": "maine",
        "मुझे": "mujhe", "मुझको": "mujhko", "मेरा": "mera", "मेरी": "meri", "मेरे": "mere",
        "हम": "hum", "हमने": "humne", "हमें": "humein", "हमे": "humein", "हमको": "humko", "हमारा": "hamara", "हमारी": "hamari", "हमारे": "hamare",
        "आप": "aap", "आपने": "aapne", "आपको": "aapko", "आपका": "aapka", "आपकी": "aapki", "आपके": "aapke",
        "तुम": "tum", "तुमने": "tumne", "तुम्हें": "tumhein", "तुम्हे": "tumhein", "तुमको": "tumko", "तुम्हारा": "tumhara", "तुम्हारी": "tumhari", "तुम्हारे": "tumhare",
        "तू": "tu", "तूने": "tune", "तुझे": "tujhe", "तेरा": "tera", "तेरी": "teri", "तेरे": "tere",
        "यह": "yeh", "ये": "ye", "इस": "is", "इसने": "isne", "इसे": "ise", "इसको": "isko", "इसका": "iska", "इसकी": "iski", "इसके": "iske", "इससे": "isse",
        "वह": "woh", "वो": "woh", "उस": "us", "उसने": "usne", "उसे": "use", "उसको": "usko", "उसका": "uska", "उसकी": "uski", "उसके": "uske", "उससे": "usse",
        "वे": "ve", "उन": "un", "उन्होंने": "unhone", "उन्होने": "unhone", "उन्हें": "unhein", "उन्हे": "unhein", "उनको": "unko", "उनका": "unka", "उनकी": "unki", "उनके": "unke",
        "इन": "in", "इन्होंने": "inhone", "इन्होने": "inhone", "इन्हें": "inhein", "इन्हे": "inhein", "इनको": "inko", "इनका": "inka", "इनकी": "inki", "इनके": "inke",
        "जो": "jo", "जिस": "jis", "जिसने": "jisne", "जिसे": "jise", "जिसको": "jisko", "जिसका": "jiska", "जिसकी": "jiski", "जिसके": "jiske", "जिससे": "jisse",
        "जिन्होंने": "jinhone", "जिन्हें": "jinhein", "जिनको": "jinko",
        "कोई": "koi", "किसी": "kisi", "किसने": "kisne", "किसे": "kise", "किसको": "kisko", "किसका": "kiska", "किसकी": "kiski", "किसके": "kiske",
        "सब": "sab", "सबने": "sabne", "सभी": "sabhi", "सबको": "sabko", "सबका": "sabka", "सब कुछ": "sab kuch", "कुछ": "kuch",

        # Conjunctions, Postpositions & Particles
        "और": "aur", "या": "ya", "लेकिन": "lekin", "पर": "par", "किंतु": "kintu", "परंतु": "parantu",
        "मगर": "magar", "क्योंकि": "kyunki", "चूंकि": "chunki", "ताकि": "taaki", "तो": "to",
        "अगर": "agar", "यदि": "yadi", "जब": "jab", "तब": "tab", "जैसे": "jaise", "वैसे": "waise",
        "भी": "bhi", "ही": "hi", "तक": "tak", "भर": "bhar", "मात्र": "matra", "सिर्फ": "sirf", "केवल": "kewal",
        "कि": "ki", "के": "ke", "की": "ki", "का": "ka", "को": "ko", "ने": "ne", "में": "mein", "मे": "mein", "से": "se", "पे": "pe",
        "साथ": "saath", "बिना": "bina", "बारे": "baare", "लिए": "liye", "फिर": "phir", "बस": "bas",

        # Question words
        "क्या": "kya", "क्यों": "kyun", "क्यो": "kyun", "कैसे": "kaise", "कब": "kab",
        "कहाँ": "kahan", "कहां": "kahan", "कहा": "kahan", "किधर": "kidhar", "कितना": "kitna", "कितनी": "kitni", "कितने": "kitne",
        "कौन": "kaun", "किस": "kis",

        # Numbers & Time
        "एक": "ek", "दो": "do", "तीन": "teen", "चार": "chaar", "पाँच": "paanch", "पांच": "paanch",
        "छह": "chhah", "सात": "saat", "आठ": "aath", "नौ": "nau", "दस": "das",
        "बजे": "baje", "घंटे": "ghante", "घंटा": "ghanta", "मिनट": "minute", "सेकंड": "second",
        "दिन": "din", "रात": "raat", "सुबह": "subah", "शाम": "shaam", "दोपहर": "dopahar",
        "आज": "aaj", "कल": "kal", "परसों": "parson", "रोज": "roz", "रोज़": "roz", "हमेशा": "hamesha", "कभी": "kabhi",
        "अभी": "abhi", "बाद": "baad", "पहले": "pehle", "जल्दी": "jaldi", "धीरे": "dheere",
        "सप्ताह": "hafta", "हफ्ता": "hafta", "महीना": "mahina", "साल": "saal",

        # Adverbs & Adjectives
        "हाँ": "haan", "हां": "haan", "नहीं": "nahi", "नही": "nahi", "मत": "mat", "ना": "na",
        "अच्छा": "achha", "अच्छी": "achhi", "अच्छे": "achhe",
        "बुरा": "bura", "बुरी": "buri", "बुरे": "bure",
        "बहुत": "bahut", "थोड़ा": "thoda", "थोड़ी": "thodi", "थोड़े": "thode", "कम": "kam", "ज्यादा": "zyada", "ज़्यादा": "zyada",
        "सही": "sahi", "गलत": "galat", "ठीक": "theek", "बढ़िया": "badhiya",
        "यहाँ": "yahan", "यहां": "yahan", "वहाँ": "wahan", "वहां": "wahan", "जहाँ": "jahan", "जहां": "jahan",
        "ज़रूर": "zaroor", "जरूर": "zaroor", "शायद": "shayad", "बिल्कुल": "bilkul",
        "कृपया": "kripya", "धन्यवाद": "dhanyawaad", "नमस्ते": "namaste",

        # Auxiliaries & Modals
        "है": "hai", "हैं": "hain", "हूँ": "hoon", "हूं": "hoon", "हो": "ho",
        "था": "tha", "थी": "thi", "थे": "the",
        "होगा": "hoga", "होगी": "hogi", "होंगे": "honge",
        "चाहिए": "chahiye", "चाहता": "chahta", "चाहती": "chahti", "चाहते": "chahte",
        "चाहूं": "chahoon", "चाहूँ": "chahoon",
        "सकता": "sakta", "सकती": "sakti", "सकते": "sakte", "सकें": "sakein", "सके": "sake", "सकेंगे": "sakenge",
        "रहा": "raha", "रही": "rahi", "रहे": "rahe",
        "गया": "gaya", "गई": "gayi", "गए": "gaye",
        "हुआ": "hua", "हुई": "hui", "हुए": "hue",
        "पड़ेगा": "padega", "पड़ेगी": "padegi", "पड़ेंगे": "padenge", "पड़ा": "pada", "पड़ी": "padi", "पड़े": "pade",

        # Verbs & Imperatives (including past/perfective forms)
        "करना": "karna", "करनी": "karni", "करने": "karne", "करता": "karta", "करती": "karti", "करते": "karte", "कर": "kar", "करो": "karo", "करें": "karein", "करेंगे": "karenge", "करूंगा": "karunga", "करूँगा": "karunga",
        "किया": "kiya", "किये": "kiye", "किए": "kiye",
        "कीजिए": "kijiye", "दीजिए": "dijiye", "लीजिए": "lijiye", "देखिए": "dekhiye", "दिखाइए": "dikhaiye", "बोलिए": "boliye", "बताइए": "bataiye", "सुनिए": "suniye", "चलिए": "chaliye",
        "देना": "dena", "देनी": "deni", "देने": "dene", "देता": "deta", "देती": "deti", "देते": "dete", "दे": "de", "दो": "do", "दें": "dein", "दिया": "diya", "दिये": "diye", "दिए": "diye", "दी": "di", "दूंगा": "dunga", "देंगे": "denge",
        "लेना": "lena", "लेनी": "leni", "लेने": "lene", "लेता": "leta", "लेती": "leti", "लेते": "lete", "ले": "le", "लो": "lo", "लें": "lein", "लिया": "liya", "लिये": "liye", "ली": "li", "लेंगे": "lenge",
        "जाना": "jaana", "जानी": "jaani", "जाने": "jaane", "जाता": "jaata", "जाती": "jaati", "जाते": "jaate", "जा": "ja", "जाओ": "jao", "जाएं": "jaayein", "जाए": "jaaye",
        "जाएगा": "jayega", "जाएगी": "jayegi", "जाएंगे": "jayenge", "जायेगा": "jayega", "जायेगी": "jayegi", "जायेंगे": "jayenge",
        "आना": "aana", "आनी": "aani", "आने": "aane", "आता": "aata", "आती": "aati", "आते": "aate", "आ": "aa", "आओ": "aao", "आएं": "aayein", "आए": "aaye", "आया": "aaya", "आई": "aayi",
        "आएगा": "aayega", "आएगी": "aayegi", "आएंगे": "aayenge",
        "बोलना": "bolna", "बोलनी": "bolni", "बोलने": "bolne", "बोलता": "bolta", "बोलती": "bolti", "बोलते": "bolte", "बोलो": "bolo", "बोलें": "bolein", "बोलूं": "boloon", "बोलूँ": "boloon",
        "बोला": "bola", "बोली": "boli", "बोले": "bole", "कहा": "kaha", "कही": "kahi", "कहे": "kahe",
        "बताना": "bataana", "बताने": "bataane", "बताता": "bataata", "बताती": "bataati", "बताते": "bataate", "बताओ": "batao", "बताएं": "bataayein", "बताया": "bataya", "बताई": "batayi", "बताए": "bataye", "बताऊंगा": "bataunga",
        "देखना": "dekhna", "देखनी": "dekhni", "देखने": "dekhne", "देखता": "dekhta", "देखती": "dekhti", "देखते": "dekhte", "देखो": "dekho", "देखें": "dekhein", "देखा": "dekha", "देखी": "dekhi", "देखे": "dekhe",
        "सुनना": "sunna", "सुननी": "sunni", "सुनने": "sunne", "सुनता": "sunta", "सुनती": "sunti", "सुनते": "sunte", "सुनो": "suno", "सुनें": "sunein", "सुना": "suna", "सुनी": "suni",
        "समझना": "samajhna", "समझनी": "samajhni", "समझने": "samajhne", "समझता": "samajhta", "समझती": "samajhti", "समझते": "samajhte", "समझ": "samajh", "समझा": "samjha", "समझी": "samjhi",
        "लगता": "lagta", "लगती": "lagti", "लगते": "lagte", "लगेगा": "lagega", "लगेगी": "lagegi", "लगेंगे": "lagenge", "लगा": "laga", "लगी": "lagi", "लगे": "lage",
        "पहुंचना": "pahunchna", "पहुंचने": "pahunchne", "पहुंचे": "pahunche", "पहुंच": "pahunch",
        "पूछा": "puchha", "पूछी": "puchhi", "पूछे": "puchhe", "पूछना": "puchhna", "पूछो": "puchho",
        "मिला": "mila", "मिली": "mili", "मिले": "mile", "मिलना": "milna", "मिलेगा": "milega", "मिलेगी": "milegi",
        "रखा": "rakha", "रखी": "rakhi", "रखे": "rakhe", "रखना": "rakhna", "रखो": "rakho",
        "भेजा": "bheja", "भेजी": "bheji", "भेजे": "bheje", "भेजना": "bhejna", "भेजो": "bhejo", "भेज": "bhej",
        "लिखा": "likha", "लिखी": "likhi", "लिखे": "likhe", "लिखना": "likhna", "लिखो": "likho",
        "सोचा": "socha", "सोची": "sochi", "सोचे": "soche", "सोचना": "sochna",
        "बनाया": "banaya", "बनाई": "banayi", "बनाए": "banaye", "बनाना": "banana",
        "चला": "chala", "चली": "chali", "चले": "chale", "चलना": "chalna", "चलो": "chalo",

        # Common nouns & proper names
        "काम": "kaam", "बात": "baat", "बातचीत": "baatcheet", "समय": "samay", "वक़्त": "waqt", "वक्त": "waqt",
        "दोस्त": "dost", "लोग": "log", "घर": "ghar", "जगह": "jagah", "तरफ": "taraf", "तरह": "tarah", "वजह": "wajah",
        "समस्या": "samasya", "दिक्कत": "dikkat", "समाधान": "samadhan", "मदद": "madad",
        "सवाल": "sawaal", "जवाब": "jawaab", "जानकारी": "jaankari", "खबर": "khabar",
        "नाम": "naam", "चीज": "cheez", "चीज़": "cheez", "बातें": "baatein", "कामों": "kaamon",
        "शुरू": "shuru", "खत्म": "khatam", "तैयार": "taiyaar", "पूरा": "poora", "पूरी": "poori", "पूरे": "poore",
        "अलग": "alag", "अगला": "agla", "अगली": "agli", "अगले": "agle", "पिछला": "pichhla", "पिछली": "pichhli", "पिछले": "pichhle",
        "हिंदी": "Hindi", "हिन्दी": "Hindi", "अंग्रेजी": "English", "अंग्रेज़ी": "English", "भारत": "Bharat", "इंडिया": "India",
        "पंकज": "Pankaj", "राहुल": "Rahul", "अमित": "Amit", "प्रिया": "Priya", "रोहित": "Rohit",
        "नेहा": "Neha", "मिलिंद": "Milind", "अंकित": "Ankit", "विकास": "Vikas", "दीपक": "Deepak",
        "गौरव": "Gaurav", "सौरभ": "Saurabh", "नीरज": "Neeraj", "पूजा": "Pooja", "सोनिया": "Sonia",
        "रवि": "Ravi", "संजय": "Sanjay", "विजय": "Vijay", "अजय": "Ajay", "मनीष": "Manish",
        "नितिन": "Nitin", "सचिन": "Sachin", "हेमंत": "Hemanth", "आस्था": "Astha", "रिया": "Riya",
        "रिदम": "Rhythm", "रुमझुम": "Rumjhum", "विनीत": "Vineet", "मनोज": "Manoj",
        "अभिलाष": "Abhilash", "अमर": "Amar", "यामिनी": "Yamini", "अनन्या": "Anannya",
    }

    FILLER_PATTERN = re.compile(
        r"\b(um+|uh+|erm+|er+|hmm+)\b[,\s]*",
        re.IGNORECASE,
    )

    CONVERSATIONAL_FILLER_PATTERNS = [
        re.compile(r"\bso\s+basically\b[,\s]*", re.IGNORECASE),
        re.compile(r"\byou\s+know\s+what\b[,\s]*", re.IGNORECASE),
        re.compile(r"\byou\s+know\b[,\s]*", re.IGNORECASE),
        re.compile(r"\bbasically\b[,\s]*", re.IGNORECASE),
        re.compile(r"\b(um+|uh+|erm+|er+|hmm+)\b[,\s]*", re.IGNORECASE),
        # Conversational 'like' as filler:
        # 1. Enclosed in commas: e.g. ", like, "
        re.compile(r"(?<=,)\s*like\s*(?=,)[,\s]*", re.IGNORECASE),
        # 2. Leading filler 'like' at beginning of sentence or after clause punctuation: "Like, we need..."
        re.compile(r"(?:^|[.!?।\n])\s*like[,\s]+", re.IGNORECASE),
        # 3. Double filler e.g. "like basically", "basically like"
        re.compile(r"\b(?:like\s+basically|basically\s+like)\b[,\s]*", re.IGNORECASE),
        # 4. Phrase-level conversational filler: "was like really", "is like totally"
        re.compile(r"\b(was|is|are|were)\s+like\s+(really|actually|just|pretty|super)\b", re.IGNORECASE),
    ]

    CORRECTION_MARKER_PATTERN = re.compile(
        r"(?P<sentence_marker>[,;]?\s*\b(?:scratch[\s,]+last[\s,]+sentence|delete[\s,]+last[\s,]+sentence|undo[\s,]+last[\s,]+sentence)\b[.\s]*)"
        r"|(?P<clause_marker>[,;]?\s*\b(?:"
        r"scratch[\s,]+that[\s,]+no[\s,]+actually"
        r"|no[\s,]+actually[\s,]+scratch[\s,]+that"
        r"|actually[\s,]+wait[\s,]+scratch[\s,]+that"
        r"|no[\s,]+wait[\s,]+scratch[\s,]+that"
        r"|wait[\s,]+no[\s,]+scratch[\s,]+that"
        r"|actually[\s,]+scratch[\s,]+that"
        r"|wait[\s,]+scratch[\s,]+that"
        r"|no[\s,]+scratch[\s,]+that"
        r"|no[\s,]+actually"
        r"|actually[\s,]+no"
        r"|actually[\s,]+wait"
        r"|wait[\s,]+actually"
        r"|wait[\s,]+sorry"
        r"|sorry[\s,]+wait"
        r"|no[\s,]+wait"
        r"|never[\s,]+mind"
        r"|nevermind"
        r"|actually"
        r"|scratch[\s,]+that"
        r"|delete[\s,]+that"
        r"|undo[\s,]+that"
        r")\b[,\s]*)"
        r"|(?P<term_marker>[,;]?\s*\b(?:i[\s,]+mean|i[\s,]+meant|correction)\b[,\s]*)",
        re.IGNORECASE,
    )

    # Common Whisper silence hallucinations to ignore
    HALLUCINATION_PHRASES = {
        "thank you.",
        "thanks for watching.",
        "thank you for watching.",
        "subtitles by the amara.org community",
        "you",
        ".",
        "।",
        "नमस्ते।",
        "नमस्ते",
        "we speak in english and hindi.",
        "we speak in english and hindi",
        "we speak in english and",
        "we speak in english",
        "we speak english and hindi",
        "hum english aur hindi me baat karte hain",
        "microsoft office word document",
        "word.document.8",
        "msworddoc",
    }

    # Spoken word numbers for entity formatting
    SPOKEN_NUMBER_WORDS = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
        "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
        "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
        "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
    }

    @classmethod
    def _is_numeric_or_currency_token(cls, token: str) -> bool:
        """Determines if a word token represents a digit, currency symbol/name, or spoken number word."""
        clean = token.lower().strip(".,!?;:")
        if not clean:
            return False
        if re.match(r"^[$₹€£]?\d+(?:\.\d+)?[%$₹€£]?$", clean):
            return True
        if clean in cls.SPOKEN_NUMBER_WORDS or clean in (
            "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
            "eighty", "ninety", "hundred", "thousand", "million", "billion", "and"
        ):
            return True
        if clean in (
            "dollar", "dollars", "buck", "bucks", "cent", "cents",
            "rupee", "rupees", "rs", "inr", "euro", "euros", "pound", "pounds",
            "percent", "percentage"
        ):
            return True
        return False

    @classmethod
    def format_numeric_entities(cls, text: str) -> str:
        """
        Intelligently formats spoken numbers, currency, percentages, versions,
        and mathematical operators without altering the speaker's true meaning.
        """
        if not text:
            return ""
        out = text

        # 1. Spoken word number + currency conversions (e.g. "fifty dollars" -> "$50")
        for word, num in cls.SPOKEN_NUMBER_WORDS.items():
            out = re.sub(rf"\b{word}\s+(?:dollars?|bucks?)\b", f"${num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b(?:dollars?|bucks?)\s+{word}\b", f"${num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b{word}\s+(?:rupees?|rs\.?|inr)\b", f"₹{num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b(?:rupees?|rs\.?)\s+{word}\b", f"₹{num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b{word}\s+(?:euros?)\b", f"€{num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b{word}\s+(?:pounds?)\b", f"£{num}", out, flags=re.IGNORECASE)
            out = re.sub(rf"\b{word}\s+(?:percent|percentage)\b", f"{num}%", out, flags=re.IGNORECASE)

        # 2. Digits + currency (e.g. "50 dollars" -> "$50", "dollar 50" -> "$50")
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:dollars?|bucks?)\b", r"$\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:dollar|dollars)\s+(\d+(?:\.\d+)?)\b", r"$\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:rupees?|rs\.?|inr)\b", r"₹\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:rupees?|rs\.?)\s+(\d+(?:\.\d+)?)\b", r"₹\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:euros?)\b", r"€\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:euro|euros)\s+(\d+(?:\.\d+)?)\b", r"€\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:pounds?)\b", r"£\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(?:pound|pounds)\s+(\d+(?:\.\d+)?)\b", r"£\1", out, flags=re.IGNORECASE)

        # 3. Percentages (e.g. "50 percent" -> "50%", "99.9 percentage" -> "99.9%")
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:percent|percentage)\b", r"\1%", out, flags=re.IGNORECASE)

        # 4. Version numbers (e.g. "version 2.0" -> "v2.0", "v 1.2" -> "v1.2")
        out = re.sub(r"\bversion\s+(\d+(?:\.\d+)+)\b", r"v\1", out, flags=re.IGNORECASE)
        out = re.sub(r"\bv\s+(\d+(?:\.\d+)+)\b", r"v\1", out, flags=re.IGNORECASE)

        # 5. Technical arithmetic between numbers (e.g. "10 plus 5" -> "10 + 5")
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s+plus\s+(\d+(?:\.\d+)?)\b", r"\1 + \2", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s+minus\s+(\d+(?:\.\d+)?)\b", r"\1 - \2", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s+times\s+(\d+(?:\.\d+)?)\b", r"\1 * \2", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s+divided\s+by\s+(\d+(?:\.\d+)?)\b", r"\1 / \2", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(\d+(?:\.\d+)?)\s+equals\s+(\d+(?:\.\d+)?)\b", r"\1 = \2", out, flags=re.IGNORECASE)

        return out

    @classmethod
    def restore_loanwords(cls, text: str) -> str:
        """
        Restores English technical, corporate, and common loanwords from Devanagari into Latin script.
        E.g. 'स्क्रीन शेयर' -> 'screen share', 'बग फिक्स' -> 'bug fix', 'माइक्रोसॉफ्ट टीम्स' -> 'Microsoft Teams'.
        Uses Devanagari-safe boundaries so combining vowel signs (matras) at word ends always match.
        """
        if not text:
            return ""
        out = text
        for pat, repl in cls.DEVA_TO_ENGLISH_LOANWORDS:
            safe_pat = pat.replace(r"\b", "")
            bound_pat = rf"(?<![\w\u0900-\u097F])(?:{safe_pat})(?![\w\u0900-\u097F])"
            out = re.sub(bound_pat, repl, out, flags=re.IGNORECASE)
        return out

    @classmethod
    def normalize_devanagari(cls, text: str) -> str:
        """Apply orthographic fixes to Devanagari text and use Purna Viram (।) at Devanagari sentence ends."""
        if not text:
            return ""
        out = text.strip()
        for wrong, right in cls.DEVA_ORTHOGRAPHIC_FIXES:
            out = out.replace(wrong, right)
        # Convert trailing ASCII period after a Devanagari character into Hindi Purna Viram (।)
        out = re.sub(r"([\u0900-\u097F])\.\s*$", r"\1।", out)
        return out

    @classmethod
    def devanagari_to_roman_hinglish(cls, text: str) -> str:
        """
        Convert Devanagari Hindi words into natural conversational Roman Hinglish
        with Hindi schwa deletion and English loanword restoration, preserving
        existing Latin/English words and punctuation.
        """
        if not text or not any("\u0900" <= ch <= "\u097F" for ch in text):
            return text

        # Step 1: Normalize orthography and convert Hindi Purna Viram to standard period
        text = cls.normalize_devanagari(text).replace("।", ".")

        # Step 2: Restore English loanwords so tech terms are cleanly in English orthography
        text = cls.restore_loanwords(text)

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

        def _translit_word(word: str) -> str:
            if word in cls.HINGLISH_WORD_MAP:
                return cls.HINGLISH_WORD_MAP[word]
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
                    # Word-final 'aa' -> 'a' in conversational Hinglish (e.g. बोलना -> bolna)
                    if m_rom == "aa" and i == n - 1 and n > 2:
                        m_rom = "a"
                    # Word-final 'ee' -> 'i' in conversational Hinglish (e.g. करनी -> karni, मेरी -> meri)
                    elif m_rom == "ee" and i == n - 1 and n > 2:
                        m_rom = "i"
                    res.append(c_rom + m_rom)
                    i += 1
                else:
                    # Medial schwa deletion heuristics:
                    # In Hindi phonology, medial consonant before suffixes like -te, -ti, -ta, -ne, -na, -ke drops inherent 'a':
                    if i < n:
                        next_part = word[i:]
                        if re.match(r"^[क-ह][ािीुूेैोौ]", next_part) and len(word) >= 3 and i >= 1:
                            res.append(c_rom)
                        else:
                            res.append(c_rom + "a")
                    else:
                        # Inherent schwa deletion at word end
                        res.append(c_rom)

            w_out = "".join(res)
            # Post-cleanups for conversational Hinglish phonetics
            w_out = re.sub(r"([aeiIoOuU])nn([aeiIoOuU])", r"\1n\2", w_out)
            w_out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]e\b)", r"\1\2", w_out)
            w_out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]a\b)", r"\1\2", w_out)
            w_out = re.sub(r"([bcdfghjklmnpqrstvwxyz])a([td]i\b)", r"\1\2", w_out)
            w_out = re.sub(r"ee$", "i", w_out)
            return w_out

        tokens = re.split(r"([^\w\u0900-\u097F]+)", text)
        converted = [_translit_word(tok) for tok in tokens]
        return "".join(converted)

    @classmethod
    def detect_voice_command(cls, raw_text: str) -> Optional[str]:
        """
        Detects standalone spoken voice commands for hands-free workflow.
        Returns command identifier or None if regular text.
        """
        cleaned = re.sub(r"[^\w\s]", "", raw_text.strip().lower())
        if cleaned in ("stop listening", "stop dictation", "stop voice typing", "stop recording"):
            return "stop_listening"
        if cleaned in ("delete that", "scratch that", "erase that", "undo that"):
            return "delete_that"
        if cleaned in ("send message", "send it", "press enter", "hit enter"):
            return "press_enter"
        if cleaned in ("new line", "next line"):
            return "new_line"
        return None

    @classmethod
    def classify_app_context(cls, window_title: str = "", process_name: str = "") -> str:
        """
        Classify the target application into Wispr Flow's context categories:
        - 'vibe_coding': VS Code, Cursor, Windsurf, PyCharm, IntelliJ, Antigravity, Terminal, PowerShell
        - 'work_messages': Slack, Microsoft Teams, Discord, Zoom, Mattermost
        - 'email': Outlook, Gmail, Superhuman, Thunderbird
        - 'personal_messages': WhatsApp, Telegram, Signal, Messenger
        - 'document': Word, Notion, Obsidian, Google Docs, Notepad, OneNote
        """
        combined = f"{window_title} {process_name}".lower()
        if any(
            k in combined
            for k in (
                "cursor", "vscode", "visual studio code", "code.exe", "windsurf", "pycharm",
                "idea64", "intellij", "antigravity", "powershell", "windowsterminal", "cmd.exe",
                "wezterm", "alacritty", "iterm", "devin", ".py", ".ts", ".tsx", ".js", ".rs", ".go"
            )
        ):
            return "vibe_coding"
        if any(k in combined for k in ("slack", "teams", "ms-teams", "discord", "mattermost", "webex")):
            return "work_messages"
        if any(k in combined for k in ("outlook", "olk.exe", "gmail", "superhuman", "thunderbird", "mail")):
            return "email"
        if any(k in combined for k in ("whatsapp", "telegram", "messenger", "signal", "wechat")):
            return "personal_messages"
        return "document"

    @classmethod
    def apply_vibe_coding_recognition(cls, text: str, window_title: str = "", process_name: str = "") -> str:
        """
        Wispr Flow 'Vibe Coding' Variable Recognition & File Tagging:
        - Formats spoken code casing directives ('snake case user id' -> 'user_id',
          'camel case user id' -> 'userId', 'pascal case user profile' -> 'UserProfile',
          'kebab case main container' -> 'main-container', 'dunder init' -> '__init__').
        - Formats spoken file tags in IDEs ('at index dot tsx' -> '@index.tsx',
          'file main dot py' -> 'main.py').
        """
        if not text:
            return text
        out = text

        def _to_snake(m: re.Match) -> str:
            words = [w.lower() for w in m.group(1).strip().split() if w]
            return "_".join(words)

        def _to_camel(m: re.Match) -> str:
            words = [w.lower() for w in m.group(1).strip().split() if w]
            if not words:
                return ""
            return words[0] + "".join(w.capitalize() for w in words[1:])

        def _to_pascal(m: re.Match) -> str:
            words = [w.lower() for w in m.group(1).strip().split() if w]
            return "".join(w.capitalize() for w in words)

        def _to_kebab(m: re.Match) -> str:
            words = [w.lower() for w in m.group(1).strip().split() if w]
            return "-".join(words)

        out = re.sub(r"\bsnake\s+case\s+([a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+){1,4})\b", _to_snake, out, flags=re.IGNORECASE)
        out = re.sub(r"\bcamel\s+case\s+([a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+){1,4})\b", _to_camel, out, flags=re.IGNORECASE)
        out = re.sub(r"\bpascal\s+case\s+([a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+){1,4})\b", _to_pascal, out, flags=re.IGNORECASE)
        out = re.sub(r"\bkebab\s+case\s+([a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+){1,4})\b", _to_kebab, out, flags=re.IGNORECASE)
        out = re.sub(r"\bdunder\s+(init|main|str|repr|len|call|enter|exit|getitem|setitem)\b", r"__\1__", out, flags=re.IGNORECASE)

        # Spoken file extensions & @file tagging in IDEs (e.g., "at index dot tsx" -> "@index.tsx")
        ext_pattern = r"(?:py|ts|tsx|js|jsx|json|md|html|css|scss|rs|go|java|cpp|cs|yaml|yml|toml|sql|sh)"
        out = re.sub(
            rf"\bat\s+([a-zA-Z0-9_-]+)\s+dot\s+({ext_pattern})\b",
            lambda m: f"@{m.group(1)}.{m.group(2).lower()}",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            rf"\b([a-zA-Z0-9_-]+)\s+dot\s+({ext_pattern})\b",
            lambda m: f"{m.group(1)}.{m.group(2).lower()}",
            out,
            flags=re.IGNORECASE,
        )
        return out

    @classmethod
    def extract_window_keywords(cls, window_title: str, process_name: str = "") -> List[str]:
        """Extract salient technical or contextual keywords from the active window title."""
        if not window_title and not process_name:
            return []
        combined = f"{window_title} {process_name}"
        # Filter common non-keyword window noise
        cleaned = re.sub(r"[\\/_\-\|\:\.\(\)\[\]]", " ", combined)
        tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 3]
        stop_words = {
            "window", "windows", "microsoft", "google", "chrome", "edge", "firefox",
            "document", "untitled", "tab", "page", "app", "application", "exe",
            "msworddoc", "word.document", "clsid", "progid", "mso", "office", "word",
            "winword", "powerpnt", "excel", "shell", "desktop", "explorer"
        }
        keywords: List[str] = []
        for tok in tokens:
            if tok.lower() not in stop_words and not tok.isdigit():
                if tok not in keywords:
                    keywords.append(tok)
            if len(keywords) >= 8:
                break
        return keywords

    @classmethod
    def build_contextual_prompt(
        cls,
        window_title: str = "",
        process_name: str = "",
        recent_history: Optional[List[str]] = None,
        language: str = "en+hi",
    ) -> str:
        """
        Build a high-precision Whisper conditioning prompt using active window context
        and recent dictation history to maintain context continuity.
        Never inject full conversational sentences (such as 'We speak in English and Hindi.')
        as Whisper will attend to prompt tokens during silence and hallucinate them into the output.
        """
        parts: List[str] = []

        # Add active window domain keywords if available
        window_keywords = cls.extract_window_keywords(window_title, process_name)
        if window_keywords:
            parts.append(f"Context: {', '.join(window_keywords[:6])}.")

        # Add recent conversational context (last 1-2 sentences, max 160 chars)
        if recent_history:
            valid_recent = [s.strip() for s in recent_history if s.strip()][-2:]
            if valid_recent:
                recent_str = " ".join(valid_recent)
                if len(recent_str) > 160:
                    recent_str = recent_str[-160:]
                parts.append(recent_str)

        return " ".join(parts).strip()

    @classmethod
    def process_interim(
        cls,
        raw_text: str,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        capitalize_first: bool = True,
        output_script: str = "auto",
    ) -> str:
        """
        Fast lightweight formatting for live provisional streaming partials.
        Renders live capitalization, accent fixes, and vocabulary replacement
        so the user sees instantaneous, high-fidelity speculative text.
        """
        if not raw_text or not raw_text.strip():
            return ""
        # Quick cleanup without aggressive terminal punctuation insertion
        text = re.sub(r"\s+", " ", raw_text).strip()
        # 1. Apply user custom vocabulary definitions first (highest priority)
        if vocabulary:
            for item in sorted(
                vocabulary,
                key=lambda v: len(str(v.get("spoken_phrase", ""))),
                reverse=True,
            ):
                if not item.get("is_active", 1):
                    continue
                spoken = str(item.get("spoken_phrase", "")).strip()
                replacement = str(item.get("replacement", "")).strip()
                if spoken and replacement:
                    text = re.sub(rf"\b{re.escape(spoken)}\b", replacement, text, flags=re.IGNORECASE)

        # 2. Apply built-in accent correction rules
        for pattern, replacement in cls.ACCENT_CORRECTION_RULES:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # 3. Apply numeric and currency formatting
        text = cls.format_numeric_entities(text)

        # 4. Apply Devanagari orthographic fixes if Devanagari present
        if any("\u0900" <= ch <= "\u097F" for ch in text):
            text = cls.normalize_devanagari(text)

        if output_script == "roman":
            text = cls.devanagari_to_roman_hinglish(text)

        if capitalize_first and text and text[0].isascii() and text[0].islower():
            text = text[0].upper() + text[1:]
        return text

    @classmethod
    def build_whisper_prompt(cls, vocabulary: List[Dict[str, Any]]) -> str:
        """
        Return empty initial_prompt so Whisper never echoes or loops vocabulary terms
        on quiet audio or background noise. Custom vocabulary replacements are applied
        deterministically in `DictationProcessor.process()`.
        """
        return ""

    @classmethod
    def is_sentence_complete(cls, processed_text: str) -> bool:
        """
        Return True if `processed_text` ends with terminal sentence punctuation (. ! ? : । \n),
        indicating that the next spoken segment should begin with a capital letter.
        Returns False if the user paused mid-sentence so the next segment continues in lowercase.
        """
        stripped = processed_text.rstrip(" ")
        if not stripped:
            return True
        if stripped.endswith("\n"):
            return True
        return bool(re.search(r"[.!?।:]\s*$", stripped))

    @classmethod
    def prune_filler_words(cls, text: str, mode: str = "cleaned") -> str:
        """
        Prunes vocal and conversational fillers ("um", "uh", "you know", "like", "basically", "so basically")
        when mode is in (cleaned, magic, structured, email).
        Preserves exact verbatim text when mode is verbatim.
        """
        if not text:
            return text
        mode_str = str(getattr(mode, "value", mode)).lower()
        if mode_str == "verbatim":
            return text

        result = text
        for pat in cls.CONVERSATIONAL_FILLER_PATTERNS:
            result = pat.sub(" ", result)

        # Cleanup resulting multiple spaces, duplicate commas, and spaces before punctuation
        result = re.sub(r"[ \t]{2,}", " ", result)
        result = re.sub(r",\s*,+", ",", result)
        result = re.sub(r"^[,\s]+", "", result)
        result = re.sub(r",\s*([.!?।])", r"\1", result)
        result = re.sub(r"\b(should|could|would|will|can|must|might|shall|we|i|you|they|he|she|it)\s*,\s*", r"\1 ", result, flags=re.IGNORECASE)
        result = re.sub(r"\s+([.,!?;:।])", r"\1", result)
        return result.strip()

    @classmethod
    def detect_verbal_self_corrections(cls, text: str) -> Dict[str, Any]:
        """
        Detects mid-utterance spoken correction markers ("no actually...", "wait sorry...",
        "scratch that...", "scratch last sentence...", "delete that...", "I mean...", "correction...").
        Retracts the erroneous preceding thought and substitutes the corrected formulation dynamically.
        Returns a dict with:
        - 'clean_text': Text with self-corrections applied.
        - 'has_corrections': True if any correction marker was detected.
        - 'retracted_segments': List of discarded preceding fragments.
        - 'annotated_html': Speech preview markup with visual red strikethrough styling.
        """
        if not text or not text.strip():
            return {
                "clean_text": text,
                "has_corrections": False,
                "retracted_segments": [],
                "annotated_html": text,
            }

        curr = text
        retracted_segments: List[str] = []

        search_pos = 0
        while True:
            m = cls.CORRECTION_MARKER_PATTERN.search(curr, search_pos)
            if not m:
                break

            pre = curr[:m.start()].strip()
            post = curr[m.end():].strip()
            raw_matched = curr[m.start():m.end()]
            matched_marker = raw_matched.strip(" ,;.")

            if m.group("sentence_marker"):
                # Retract the preceding sentence
                pre_clean = pre.rstrip(".!?। \t\n")
                sent_splits = list(re.finditer(r"[.!?।\n]\s*", pre_clean))
                if sent_splits:
                    last_bound = sent_splits[-1].end()
                    retained_prefix = pre[:last_bound].strip()
                    discarded = pre[last_bound:].strip()
                else:
                    retained_prefix = ""
                    discarded = pre

                retract_full = f"{discarded} {matched_marker}".strip()
                retracted_segments.append(retract_full)
                curr = f"{retained_prefix} {post}".strip() if retained_prefix else post
                search_pos = 0

            elif m.group("clause_marker"):
                # Guard against false positives like "don't delete that file"
                is_negated = bool(re.search(r"\b(?:don't|dont|do not|never|cannot|can't)\s*$", pre, re.IGNORECASE))
                is_transitive_object = bool(re.match(r"^(?:file|folder|document|email|message|database|record|item|row|column|line|page|table|entry|photo|picture|image|video|recording|account|app|card|code|branch|commit)\b", post, re.IGNORECASE))
                is_existential_scratch = bool(
                    re.search(r"\b(?:has|have|had|is|was|were|there|there's|found|saw|see|seen)(?:\s+[a-z]+)?\s*(?:no|a|any)?\s*$", pre, re.IGNORECASE)
                    and re.search(r"^(?:no\s+)?scratch\s+that\b", matched_marker, re.IGNORECASE)
                )

                # Linguistic guard: "no wait is fine", "no wait for me"
                is_no_wait_guard = (
                    bool(re.fullmatch(r"(?:no\s+)?wait", matched_marker.strip(), re.IGNORECASE))
                    and (
                        not pre.strip()
                        or bool(re.search(r"[.!?।\n]\s*$", pre))
                        or bool(re.match(r"^(?:is|was|are|were|has|had|for|that's|thats|will|would)\b", post, re.IGNORECASE))
                    )
                )

                # Linguistic guard: "I actually like it", "is actually pretty good"
                is_adverbial_actually = (
                    bool(re.search(r"^actually\b", matched_marker, re.IGNORECASE))
                    and (
                        bool(re.search(r"\b(?:i|you|he|she|we|they|it|that|this)\s*$", pre, re.IGNORECASE))
                        or bool(re.match(r"^(?:like|prefer|want|need|think|know|feel|see|saw|is|are|was|were|did|does|do|good|bad|fine|great|pretty|super|have|had|has|work|works|enjoy)\b", post, re.IGNORECASE))
                    )
                )

                # Linguistic guard: "never mind that file" (transitive imperative at start of clause/sentence)
                is_nevermind_transitive = (
                    bool(re.search(r"^never\s*mind\b", matched_marker, re.IGNORECASE))
                    and (not pre.strip() or bool(re.search(r"[.!?।\n]\s*$", pre)))
                    and bool(re.match(r"^(?:that|this|the|my|your|his|her|our|their)\s+[a-z]+", post, re.IGNORECASE))
                )

                if (
                    is_negated
                    or is_transitive_object
                    or is_existential_scratch
                    or is_no_wait_guard
                    or is_adverbial_actually
                    or is_nevermind_transitive
                ):
                    search_pos = m.end()
                    continue

                # E.g. 'delete that' with no subsequent text -> retract preceding clause or sentence
                if not post:
                    sent_splits = list(re.finditer(r"[.!?।\n]\s*", pre))
                    if sent_splits:
                        last_bound = sent_splits[-1].end()
                        retained_prefix = pre[:last_bound].strip()
                        discarded = pre[last_bound:].strip()
                    else:
                        retained_prefix = ""
                        discarded = pre
                    retract_full = f"{discarded} {matched_marker}".strip()
                    retracted_segments.append(retract_full)
                    curr = retained_prefix
                    search_pos = 0
                else:
                    # Check if post is a short substitution (1-3 words) vs a new clause
                    words_post = post.split()
                    sent_splits = list(re.finditer(r"[.!?।\n]\s*", pre))
                    clause_prefix = pre[sent_splits[-1].end():].strip() if sent_splits else pre
                    retained_prefix = pre[:sent_splits[-1].end()].strip() if sent_splits else ""

                    words_pre = clause_prefix.split()
                    first_post_word = words_post[0].lower().rstrip(".,!?") if words_post else ""

                    # Check for numerical / currency retractions
                    post_is_numeric = bool(words_post and cls._is_numeric_or_currency_token(words_post[0]))
                    pre_is_numeric = bool(words_pre and cls._is_numeric_or_currency_token(words_pre[-1]))
                    num_span_len = 0
                    if post_is_numeric and pre_is_numeric:
                        for w in reversed(words_pre):
                            if cls._is_numeric_or_currency_token(w):
                                num_span_len += 1
                            else:
                                break

                    is_short_sub = (
                        len(words_post) <= 3
                        and first_post_word not in ("first", "let", "let's", "we", "i", "you", "send", "make", "do", "go", "start")
                    )

                    if (post_is_numeric and pre_is_numeric and num_span_len > 0) or (is_short_sub and clause_prefix):
                        sub_len = num_span_len if (post_is_numeric and pre_is_numeric and num_span_len > 0) else min(len(words_post), len(words_pre))
                        discarded_phrase = " ".join(words_pre[-sub_len:])
                        kept_pre = " ".join(words_pre[:-sub_len])
                        retract_full = f"{discarded_phrase} {matched_marker}".strip()
                        retracted_segments.append(retract_full)
                        new_clause = f"{kept_pre} {post}".strip()
                        curr = f"{retained_prefix} {new_clause}".strip() if retained_prefix else new_clause
                    else:
                        # Full clause retraction
                        retract_full = f"{clause_prefix} {matched_marker}".strip()
                        retracted_segments.append(retract_full)
                        curr = f"{retained_prefix} {post}".strip() if retained_prefix else post
                    search_pos = 0

            elif m.group("term_marker"):
                # Term-level substitution: e.g. "Meeting is on Tuesday I mean Wednesday"
                sent_splits = list(re.finditer(r"[.!?।\n]\s*", pre))
                clause_prefix = pre[sent_splits[-1].end():].strip() if sent_splits else pre
                retained_prefix = pre[:sent_splits[-1].end()].strip() if sent_splits else ""

                words_pre = clause_prefix.split()
                words_post = post.split()

                post_is_numeric = bool(words_post and cls._is_numeric_or_currency_token(words_post[0]))
                pre_is_numeric = bool(words_pre and cls._is_numeric_or_currency_token(words_pre[-1]))
                num_span_len = 0
                if post_is_numeric and pre_is_numeric:
                    for w in reversed(words_pre):
                        if cls._is_numeric_or_currency_token(w):
                            num_span_len += 1
                        else:
                            break

                if post_is_numeric and pre_is_numeric and num_span_len > 0:
                    term_len = num_span_len
                elif len(words_post) >= 2 and words_post[1].lower() in (
                    "dollars", "percent", "users", "servers", "points", "items", "minutes", "hours", "days"
                ):
                    term_len = 2
                elif len(words_post) <= 2:
                    term_len = len(words_post)
                else:
                    term_len = 1

                post_term = " ".join(words_post[:term_len]).rstrip(".,!?")
                rest_post = " ".join(words_post[term_len:])
                trailing_punct = ""
                if not rest_post:
                    punct_m = re.search(r"([.,!?;:।]+)$", post)
                    if punct_m:
                        trailing_punct = punct_m.group(1)

                pre_term = " ".join(words_pre[-term_len:]) if words_pre else ""
                kept_pre = " ".join(words_pre[:-term_len]) if len(words_pre) > term_len else ""
                retract_full = f"{pre_term} {matched_marker}".strip()
                retracted_segments.append(retract_full)

                new_clause = f"{kept_pre} {post_term}{trailing_punct} {rest_post}".strip()
                curr = f"{retained_prefix} {new_clause}".strip() if retained_prefix else new_clause
                search_pos = 0

        # Build annotated HTML with red strikethrough for retracted segments
        html = text
        for seg in retracted_segments:
            if not seg:
                continue
            if seg in html:
                html = html.replace(
                    seg,
                    f'<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">{seg}</span>',
                )
            else:
                seg_tokens = [re.escape(tok) for tok in seg.split()]
                seg_pattern = re.compile(r"\b" + r"[,;]?\s+".join(seg_tokens) + r"\b", re.IGNORECASE)
                html = seg_pattern.sub(
                    lambda m: f'<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">{m.group(0)}</span>',
                    html,
                    count=1,
                )

        return {
            "clean_text": curr,
            "has_corrections": len(retracted_segments) > 0,
            "retracted_segments": retracted_segments,
            "annotated_html": html,
        }

    @classmethod
    def parse_verbal_self_correction(cls, text: str) -> str:
        """Convenience method returning the cleanly corrected string."""
        return cls.detect_verbal_self_corrections(text)["clean_text"]

    @classmethod
    def transform_mode(cls, text: str, mode: str = "cleaned") -> str:
        """
        Applies mode-specific transformations:
        - VERBATIM: Exact verbatim transcript.
        - CLEANED: Filler removal, spacing, capitalization, punctuation normalization.
        - MAGIC: Polished prose rewriting, removing ramblings, enhancing articulation.
        - STRUCTURED: Formats discourse into markdown headers, bullet points, and action items.
        - EMAIL: Formats discourse into executive email with subject, salutation, body, and sign-off.
        """
        if not text or not text.strip():
            return text

        mode_str = str(getattr(mode, "value", mode)).lower()
        if mode_str == "verbatim":
            return text

        cleaned = text.strip()
        if mode_str in ("clean", "cleaned", "default"):
            return cleaned

        if mode_str in ("polished", "magic"):
            paragraphs = cleaned.split("\n\n")
            polished_paras = []
            for p in paragraphs:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", p) if s.strip()]
                polished = []
                for s in sentences:
                    if s and s[0].islower():
                        s = s[0].upper() + s[1:]
                    if s and not s[-1] in (".", "!", "?", "।"):
                        s += "."
                    polished.append(s)
                if polished:
                    polished_paras.append(" ".join(polished))
            return "\n\n".join(polished_paras) if polished_paras else cleaned

        if mode_str == "structured":
            # Protect possessive/determiner phrases like "our first release", "the second time"
            protected = re.sub(
                r"\b(our|my|your|his|her|their|the|its)\s+(first|second|third)\b",
                r"\1 __ORD_\2__",
                cleaned,
                flags=re.IGNORECASE,
            )
            raw_splits = re.split(
                r"(?:^|\s*)(?:\b(?:firstly|secondly|thirdly|first|second|third|finally|next|also|bullet|item)\b|\d+\.|\-|\*)\s*[:,-]?\s*",
                protected,
                flags=re.IGNORECASE,
            )
            raw_splits = [
                re.sub(r"__ORD_(first|second|third)__", r"\1", it, flags=re.IGNORECASE).strip()
                for it in raw_splits
                if it.strip()
            ]

            if len(raw_splits) > 1:
                header = "**Key Points & Outline:**\n"
                bullets = [f"- {it[0].upper() + it[1:]}" if it else "" for it in raw_splits]
                return header + "\n".join(bullets)
            else:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", cleaned) if s.strip()]
                if len(sentences) > 1:
                    header = "**Summary:**\n"
                    bullets = [f"- {s[0].upper() + s[1:]}" if s else "" for s in sentences]
                    return header + "\n".join(bullets)
                else:
                    return f"- {cleaned[0].upper() + cleaned[1:]}"

        if mode_str == "email":
            words = cleaned.split()
            subject_phrase = " ".join(words[:5]).rstrip(".,!?:")
            subject_cap = subject_phrase[0].upper() + subject_phrase[1:] if subject_phrase else ""
            subject = f"Subject: Follow-up: {subject_cap}\n\n"
            greeting = "Hi Team,\n\n"
            body = f"{cleaned[0].upper() + cleaned[1:]}\n\n"
            if not body.strip().endswith((".", "!", "?")):
                body = body.rstrip() + ".\n\n"
            cta = "Please let me know your thoughts or if you need any adjustments.\n\n"
            sign_off = "Best regards,\nVOX Assistant"
            return f"{subject}{greeting}{body}{cta}{sign_off}"

        return cleaned

    @classmethod
    def process_text(
        cls,
        raw_text: str,
        mode: str = "cleaned",
        strip_retractions: bool = True,
        **kwargs: Any,
    ) -> DictationProcessedResult:
        """
        Milestone 2 Canonical Entrypoint:
        Runs verbal retraction detection and transformation mode pipeline,
        returning a structured DictationProcessedResult.
        """
        if not raw_text:
            return DictationProcessedResult(
                raw_text="",
                formatted_text="",
                mode=str(getattr(mode, "value", mode)).lower(),
                has_retractions=False,
                retracted_segments=[],
                annotated_html="",
            )

        retraction_info = cls.detect_verbal_self_corrections(raw_text)
        effective_raw = retraction_info["clean_text"] if strip_retractions else raw_text
        formatted = cls.process(raw_text=effective_raw, mode=mode, **kwargs)

        return DictationProcessedResult(
            raw_text=raw_text,
            formatted_text=formatted,
            mode=str(getattr(mode, "value", mode)).lower(),
            has_retractions=retraction_info["has_corrections"],
            retracted_segments=retraction_info["retracted_segments"],
            annotated_html=retraction_info["annotated_html"],
        )

    @classmethod
    def apply_snippets(
        cls,
        text: str,
        snippets: List[Dict[str, Any]] | None = None,
    ) -> tuple[str, List[int]]:
        """
        Expand registered voice snippet trigger phrases (longest trigger first)
        into their configured template expansions. Returns (updated_text, matched_ids).
        """
        if not text or not snippets:
            return text, []
        matched_ids: List[int] = []
        updated = text
        sorted_snips = sorted(
            snippets,
            key=lambda s: len(str(s.get("trigger_phrase", ""))),
            reverse=True,
        )
        for snip in sorted_snips:
            trig = str(snip.get("trigger_phrase", "")).strip()
            exp = str(snip.get("expansion", "")).strip()
            if not trig or not exp:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(trig)}(?!\w)", re.IGNORECASE)
            if pattern.search(updated):
                updated = pattern.sub(lambda _m, rep=exp: rep, updated)
                if snip.get("id") is not None:
                    matched_ids.append(int(snip["id"]))
        return updated, matched_ids

    @classmethod
    def apply_cleanup_intensity(cls, text: str, level: str = "light") -> str:
        """Apply light/medium/high prose cleanup on top of base filler pruning."""
        lvl = (level or "light").lower().strip()
        if not text or lvl in ("none", "light"):
            return text
        out = text
        if lvl in ("medium", "high"):
            out = re.sub(
                r"\b(?:to be honest|you know what i mean|you know|if that makes sense|so yeah|basically|actually)\b[, ]*",
                "",
                out,
                flags=re.IGNORECASE,
            )
        if lvl == "high":
            out = re.sub(
                r"^(?:so|well|anyway|right|look)\s*,\s*",
                "",
                out,
                flags=re.IGNORECASE,
            )
            out = re.sub(r"\s{2,}", " ", out).strip()
            if out and out[0].isascii() and out[0].islower():
                out = out[0].upper() + out[1:]
        return re.sub(r"[ \t]{2,}", " ", out).strip()

    @classmethod
    def apply_app_style(
        cls,
        text: str,
        style: str = "formal",
    ) -> str:
        """
        Format text according to Wispr Flow's 4 tone styles:
        - formal: standard capitalization & ending punctuation
        - casual: natural capitals, omit trailing period on single short message
        - very_casual: lowercase phrasing without trailing period (preserving 'I' or URLs)
        - excited: energetic punctuation replacing calm trailing period with '!'
        """
        if not text or not text.strip():
            return text
        st = (style or "formal").lower().strip()
        out = text.strip()
        if st == "formal":
            if out and out[0].isascii() and out[0].islower():
                out = out[0].upper() + out[1:]
            return out
        if st == "casual":
            # Strip single trailing period for short chat messages; keep ? and !
            if out.endswith(".") and not out.endswith("..") and out.count(".") == 1:
                out = out[:-1].rstrip()
            return out
        if st == "very_casual":
            # Lowercase ordinary sentences while keeping URLs and explicit ALL-CAPS acronyms
            words = out.split(" ")
            norm_words = []
            for idx, w in enumerate(words):
                clean_w = re.sub(r"[^\w]", "", w)
                if clean_w == "I" or (len(clean_w) >= 2 and clean_w.isupper()) or "://" in w or "@" in w:
                    norm_words.append(w)
                elif idx == 0 or (w and w[0].isupper() and not w.isupper()):
                    norm_words.append(w[0].lower() + w[1:] if len(w) > 1 else w.lower())
                else:
                    norm_words.append(w)
            out = " ".join(norm_words)
            if out.endswith(".") and not out.endswith(".."):
                out = out[:-1].rstrip()
            return out
        if st == "excited":
            if out.endswith("."):
                out = out[:-1] + "!"
            elif not out.endswith(("!", "?")):
                out = out + "!"
            return out
        return out

    @classmethod
    def transform_preset(cls, text: str, instruction: str = "polish") -> str:
        """Transform text locally using a preset name or freeform instruction."""
        if not text or not text.strip():
            return text
        cmd = (instruction or "polish").lower().strip()
        if any(k in cmd for k in ("bullet", "list", "outline", "structured")):
            return cls.transform_mode(text, mode="structured")
        if "email" in cmd:
            return cls.transform_mode(text, mode="email")
        if any(k in cmd for k in ("concise", "shorter", "short", "tight")):
            compacted = cls.apply_cleanup_intensity(text.strip(), level="high")
            return cls.transform_mode(compacted, mode="polished")
        if any(k in cmd for k in ("casual", "friendly")):
            return cls.apply_app_style(text.strip(), style="casual")
        if "formal" in cmd or "professional" in cmd:
            polished = cls.transform_mode(cls.apply_cleanup_intensity(text.strip(), level="medium"), mode="polished")
            return cls.apply_app_style(polished, style="formal")
        if any(k in cmd for k in ("hinglish", "roman")):
            return cls.devanagari_to_roman_hinglish(text)
        return cls.transform_mode(text.strip(), mode="polished")

    @classmethod
    def process_detailed(
        cls,
        raw_text: str,
        vocabulary: List[Dict[str, Any]] | None = None,
        snippets: List[Dict[str, Any]] | None = None,
        cleanup_enabled: bool = True,
        spoken_punctuation: bool | None = None,
        smart_leading_space: bool = False,
        capitalize_first: bool = True,
        output_script: str = "auto",
        mode: str = "cleaned",
        window_title: str = "",
        process_name: str = "",
        style_preferences: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Full dictation pipeline returning transformed text + metadata for UI Undo AI Edit & metrics."""
        app_category = cls.classify_app_context(window_title, process_name)
        prefs = style_preferences or {}
        cleanup_level = str(prefs.get("auto_cleanup_level", "light")).lower()
        effective_cleanup = cleanup_enabled and (cleanup_level != "none")
        final_text = cls.process(
            raw_text=raw_text,
            vocabulary=vocabulary,
            cleanup_enabled=effective_cleanup,
            spoken_punctuation=spoken_punctuation,
            smart_leading_space=smart_leading_space,
            capitalize_first=capitalize_first,
            output_script=output_script,
            mode=mode,
            window_title=window_title,
            process_name=process_name,
            snippets=snippets,
            style_preferences=prefs,
        )
        matched_ids: List[int] = []
        if snippets and final_text:
            _, matched_ids = cls.apply_snippets(raw_text, snippets)
        cat_key = {
            "work_messages": "style_work",
            "email": "style_email",
            "personal_messages": "style_personal",
        }.get(app_category, "style_other")
        applied_style = str(prefs.get(cat_key, "formal"))
        return {
            "raw_asr_text": raw_text,
            "final_text": final_text,
            "app_category": app_category,
            "applied_style": applied_style,
            "auto_cleanup_level": cleanup_level,
            "matched_snippet_ids": matched_ids,
        }

    @classmethod
    def process(
        cls,
        raw_text: str,
        vocabulary: List[Dict[str, Any]] | None = None,
        cleanup_enabled: bool = True,
        spoken_punctuation: bool | None = None,
        smart_leading_space: bool = False,
        capitalize_first: bool = True,
        output_script: str = "auto",
        mode: str = "cleaned",
        window_title: str = "",
        process_name: str = "",
        snippets: List[Dict[str, Any]] | None = None,
        style_preferences: Dict[str, Any] | None = None,
    ) -> str:
        if not raw_text or not raw_text.strip():
            return ""

        mode_str = str(getattr(mode, "value", mode)).lower()
        if mode_str == "verbatim":
            cleanup_enabled = False
            if spoken_punctuation is None:
                spoken_punctuation = False
        elif spoken_punctuation is None:
            spoken_punctuation = True

        text = raw_text.strip()

        # 0. Strip Whisper's mid-sentence trailing ellipses ("..." or "…") BEFORE period normalization
        had_trailing_ellipsis = bool(re.search(r"(?:\.{2,}|…)\s*$", text))
        if had_trailing_ellipsis:
            text = re.sub(r"(?:\.{2,}|…)\s*$", "", text).strip()
        text = re.sub(r"(?:\.{3,}|…)", " ", text)

        # 0b. Normalize Devanagari orthography (or transliterate to Roman Hinglish if requested)
        if any("\u0900" <= ch <= "\u097F" for ch in text):
            text = cls.normalize_devanagari(text)
            if output_script == "roman":
                text = cls.devanagari_to_roman_hinglish(text)

        # 0c. Expand user snippets before cleanup so long structured templates aren't mangled
        if snippets and mode_str != "verbatim":
            text, _ = cls.apply_snippets(text, snippets)

        # 0d. Real-Time Verbal Self-Correction
        if cleanup_enabled:
            text = cls.parse_verbal_self_correction(text)

        # 1. Remove filler words if cleanup mode is enabled
        if cleanup_enabled:
            text = cls.prune_filler_words(text, mode=mode_str)
            if style_preferences and style_preferences.get("auto_cleanup_level"):
                text = cls.apply_cleanup_intensity(text, level=str(style_preferences["auto_cleanup_level"]))

        # 2. Apply custom vocabulary replacements from personal database (highest priority)
        custom_replacements_lower = set()
        if vocabulary:
            sorted_vocab = sorted(
                [v for v in vocabulary if v.get("is_active", 1)],
                key=lambda x: len(str(x.get("spoken_phrase", ""))),
                reverse=True,
            )
            for item in sorted_vocab:
                spoken = str(item.get("spoken_phrase", "")).strip()
                replacement = str(item.get("replacement", "")).strip()
                if not spoken or not replacement:
                    continue
                custom_replacements_lower.add(replacement.split()[0].lower())
                pattern = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)", re.IGNORECASE)
                text = pattern.sub(replacement, text)

        # 3. Apply built-in accent & phonetic normalization rules
        for pattern_str, replacement in cls.ACCENT_CORRECTION_RULES:
            text = re.sub(pattern_str, replacement, text, flags=re.IGNORECASE)

        # 4. Convert spoken punctuation commands
        if spoken_punctuation:
            for pattern_str, symbol in cls.SPOKEN_PUNCTUATION_RULES:
                text = re.sub(
                    rf"[, \t]*{pattern_str}[\., \t]*",
                    lambda m, s=symbol: s if s.startswith("\n") else f"{s} ",
                    text,
                    flags=re.IGNORECASE,
                )

        # 4b. Format spoken numbers, currencies, percentages, math, and Vibe Coding symbols
        if mode_str != "verbatim":
            text = cls.format_numeric_entities(text)
            vibe_enabled = True if style_preferences is None else bool(style_preferences.get("vibe_coding_variable_recognition", True))
            if vibe_enabled:
                text = cls.apply_vibe_coding_recognition(
                    text, window_title=window_title, process_name=process_name
                )

        # 5. Normalize whitespace, hanging connectors, and context-aware capitalization
        if cleanup_enabled:
            # Remove spaces before punctuation marks
            text = re.sub(r"\s+([.,!?;:।\)])", r"\1", text)
            # Remove space after opening parenthesis
            text = re.sub(r"(\()\s+", r"\1", text)
            # Collapse multiple horizontal spaces
            text = re.sub(r"[ \t]{2,}", " ", text)
            # Clean spaces around newlines
            text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
            # Deduplicate accidental double periods/commas from spoken punctuation
            text = re.sub(r"\.{2,}", ".", text)
            text = re.sub(r"।\s*[\.।]+", "।", text)
            text = re.sub(r",{2,}", ",", text)

            # Strip accidental period/purna-viram if segment ends on a hanging conjunction/preposition
            if cls.HANGING_CONNECTOR_END_RE.search(text):
                text = re.sub(r"[.।]\s*$", "", text)
            elif len(text.split()) <= 7 and cls.HANGING_MODAL_END_RE.search(text):
                text = re.sub(r"\.\s*$", "", text)

            text = text.strip()
            if text:
                if capitalize_first:
                    text = text[0].upper() + text[1:]
                else:
                    first_token = re.sub(r"^[^\w\u0900-\u097F]*", "", text.split()[0])
                    first_clean = re.sub(r"[^\w']", "", first_token)
                    is_acronym = len(first_clean) >= 2 and first_clean.isupper()
                    keep_cap = (
                        is_acronym
                        or first_clean.lower() in cls.ALWAYS_CAPITALIZE_WORDS
                        or first_clean.lower() in custom_replacements_lower
                    )
                    if not keep_cap and text[0].isascii() and text[0].isalpha():
                        text = text[0].lower() + text[1:]
                    elif first_clean.lower() in ("i", "i'm", "i'll", "i've", "i'd") and text[0].isalpha():
                        text = text[0].upper() + text[1:]

                # Always capitalize after internal sentence boundaries (. ! ? । \n)
                text = re.sub(
                    r"([.!?।]\s+|\n+)([a-z])",
                    lambda m: m.group(1) + m.group(2).upper(),
                    text,
                )

        text = text.strip()
        if mode_str not in ("clean", "cleaned", "default", "verbatim"):
            text = cls.transform_mode(text, mode=mode_str)
        elif style_preferences and mode_str != "verbatim" and cleanup_enabled:
            app_cat = cls.classify_app_context(window_title, process_name)
            cat_key = {
                "work_messages": "style_work",
                "email": "style_email",
                "personal_messages": "style_personal",
            }.get(app_cat, "style_other")
            tone = str(style_preferences.get(cat_key, "formal"))
            text = cls.apply_app_style(text, style=tone)
        return text

    ensure_english_script = devanagari_to_roman_hinglish



