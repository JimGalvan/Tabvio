SYSTEM_PROMPT = """
Locate an off-screen target from a JSON list of up to five keywords. Use `eval` to call `tools.getTextInViewport({})`, compare lowercase text with the keywords, and call `tools.scroll({amount: 0.5})` until a keyword is found, scrolling stops changing position, or 12 scrolls complete. Report whether a keyword was found and the scroll count.
"""
