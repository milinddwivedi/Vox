import sys
sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from voxlocal.engine.dictation_processor import DictationProcessor

tests = [
    "अगर मैं हिंदी में बोलना चाहूं, तो अच्छे से टाइप हो जाना चाहिए.",
    "आज client के साथ meeting है, so please prepare the project update.",
    "I am testing Microsoft Teams, SQLite database and Python speech recognition on Windows.",
    "कृपया कल सुबह 9 बजे की मीटिंग रिशेड्यूल कर दीजिए, मुझे office पहुंचने में थोड़ा समय लगेगा.",
    "API endpoint deploy हो गया है. अब frontend में bug fix करना पड़ेगा.",
]

for t in tests:
    print("INPUT :", t)
    print("ROMAN :", DictationProcessor.process(t, output_script="roman"))
    print("-" * 50)
