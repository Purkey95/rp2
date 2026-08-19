"""Pin the taxonomy prioritizer. Run: python3 test_prioritize.py"""

import json
import os

from prioritize import priority, ACCESSIBILITY

HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    base = {"value": 5, "reliability": 5, "effort": 2}
    ok &= check("wired priority = v*r*1.0/e", priority({**base, "availability": "wired"}), 12.5)
    ok &= check("env-free discounted", priority({**base, "availability": "env-free"}), 11.25)

    # same value/reliability/effort, but gated behind browser records -> ranks lower
    gated = priority({**base, "availability": "rod-browser"})
    ok &= check("gated ranks below derived", gated < priority({**base, "availability": "derived"}), True)
    ok &= check("not-obtainable excluded (0)", priority({**base, "availability": "not-obtainable"}), 0.0)
    ok &= check("accessibility ordering",
                ACCESSIBILITY["derived"] > ACCESSIBILITY["free-add"] > ACCESSIBILITY["sos-paid"] > ACCESSIBILITY["court-browser"],
                True)

    # taxonomy loads and every signal has the scoring attributes
    tax = json.load(open(os.path.join(HERE, "signal_taxonomy.json"), encoding="utf-8"))
    ok &= check("every signal has attributes",
                all(all(k in s for k in ("value", "reliability", "effort", "availability", "dimension", "family"))
                    for s in tax["signals"]), True)
    # every signal is placed in one of the seven arbitrages, and that class is declared
    classes = set(tax["arbitrage_classes"])
    ok &= check("every signal has an arbitrage_class",
                all("arbitrage_class" in s for s in tax["signals"]), True)
    ok &= check("every arbitrage_class is declared",
                all(s["arbitrage_class"] in classes for s in tax["signals"]), True)
    # the strategic point: all seven money-making classes are represented, not just distress
    covered = {s["arbitrage_class"] for s in tax["signals"]}
    ok &= check("all seven arbitrages represented",
                {"distress", "timing", "complexity", "development", "relationship", "information", "catalyst"} <= covered,
                True)
    # the top build-next signal is free/derived, not a paid/browser one (the whole thesis)
    todo = [s for s in tax["signals"] if s["status"] != "built" and s["availability"] != "not-obtainable"]
    for s in todo:
        s["_p"] = priority(s)
    top = max(todo, key=lambda s: s["_p"])
    ok &= check("top build-next is cheaply-available",
                top["availability"] in ("wired", "derived", "env-free", "free-add"), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
