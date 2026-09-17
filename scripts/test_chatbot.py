import pandas as pd
from dashboard import extract_stock_symbol, df_instruments, IGNORE_WORDS

tests = [
    # Query, Expected extracted symbol (None if FAQ/etc.)
    ("do you know about stltech", "STLTECH"),
    ("what is the price of AAPL", "AAPL"),
    ("do you have TCS stock", "TCS"),
    ("List the output files", None),
    ("how to run this project", None),
    ("which brokers are supported?", None),
    ("tell me about gold", "GOLD"),
    ("is the data live or delayed?", None),
    ("hey there assistant", None),
    ("Tell me about RELIANCE", "RELIANCE")
]

print("=" * 60)
print("RUNNING CHATBOT SYMBOL EXTRACTION EVALUATION (10 TEST CASES)")
print("=" * 60)

passed = 0
for idx, (query, expected) in enumerate(tests, 1):
    result = extract_stock_symbol(query, df_instruments)
    status = "PASS" if result == expected else "FAIL"
    if status == "PASS":
        passed += 1
    print(f"Test {idx:02d}: '{query}'")
    print(f"  Expected: {expected}")
    print(f"  Got:      {result}")
    print(f"  Status:   {status}")
    print("-" * 60)

print(f"Evaluation finished: {passed}/{len(tests)} cases passed.")
print("=" * 60)

if passed == len(tests):
    print("ALL TESTS PASSED SUCCESSFULLY!")
else:
    print("SOME TESTS FAILED. PLEASE REFINE LOGIC.")
