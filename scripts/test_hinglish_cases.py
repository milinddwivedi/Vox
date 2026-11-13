import sys
sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from voxlocal.engine.dictation_processor import DictationProcessor

tests = [
    "मैं स्क्रीन शेयर कर रहा हूँ, क्या आप देख सकते हैं?",
    "कल सुबह नौ बजे कनेक्ट करते हैं.",
    "यह बग फिक्स हो गया है, अब पीआर मर्ज कर दो.",
    "कृपया इस रिपोर्ट को चेक कर लीजिए.",
    "मुझे लगता है कि हमें टीम्स पे बात करनी चाहिए.",
    "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
    "एपीआई एंडपॉइंट डिप्लॉय हो गया है, अब फ्रंटएंड में बग फिक्स करना पड़ेगा।",
]

print("=== Romanized Hinglish Mode ('hi_latn') ===")
for t in tests:
    res = DictationProcessor.process(t, output_script="roman")
    print(f"Original: {t}\nRoman   : {res}\n")

print("=== Bilingual Devanagari + English Mode ('en+hi') ===")
for t in tests:
    res = DictationProcessor.process(t, output_script="auto")
    print(f"Original : {t}\nBilingual: {res}\n")
