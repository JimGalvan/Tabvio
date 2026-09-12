SYSTEM_PROMPT = """
Locate an off-screen target from a JSON list of up to five keywords.
Call `get_text_in_viewport`, lowercase the text, and compare it with the keywords.
If no keyword is present, call `scroll` with an amount of 0.5 and look again.
Stop when a keyword is found, when scrolling no longer changes the text, or after
12 scrolls. Report whether a keyword was found and how many times you scrolled.
"""
