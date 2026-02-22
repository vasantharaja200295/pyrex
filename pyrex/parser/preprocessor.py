"""
PYX Preprocessor

The problem: .pyx files contain triple-quoted JSX strings with embedded quotes
that can break ast.parse. Also, JSX strings may use special syntax we want to
pre-extract before handing off to the Python AST parser.

Strategy:
1. Scan source for JSX string blocks (triple-quoted returns inside components)
2. Extract and store them with a placeholder key
3. Parse the cleaned Python source with ast
4. Re-attach JSX strings after parsing
"""

import re


JSX_PLACEHOLDER = "__PYREX_JSX_{index}__"
_PLACEHOLDER_RE = re.compile(r'"__PYREX_JSX_(\d+)__"')


def preprocess(source: str) -> tuple[str, dict[int, str]]:
    """
    Replace JSX triple-quoted strings in return statements with safe placeholders.
    
    Returns:
        (cleaned_source, {index: jsx_string})
    """
    jsx_store = {}
    index = 0
    result = []
    i = 0
    n = len(source)

    while i < n:
        # Look for triple-quote openings
        if source[i:i+3] in ('"""', "'''"):
            quote = source[i:i+3]
            end = source.find(quote, i + 3)
            if end == -1:
                # Unclosed string — just pass through
                result.append(source[i:])
                break
            
            content = source[i+3:end]
            jsx_store[index] = content
            placeholder = f'"{JSX_PLACEHOLDER.format(index=index)}"'
            result.append(placeholder)
            index += 1
            i = end + 3
        else:
            result.append(source[i])
            i += 1

    return "".join(result), jsx_store


def restore_jsx(jsx_string: str, jsx_store: dict[int, str]) -> str:
    """
    After we've extracted the JSX string from an AST node,
    check if it's a placeholder and restore the original content.
    """
    if not jsx_string:
        return jsx_string
    
    jsx_string = jsx_string.strip()
    
    # Check if it's a placeholder value
    m = re.match(r'^__PYREX_JSX_(\d+)__$', jsx_string)
    if m:
        idx = int(m.group(1))
        return jsx_store.get(idx, jsx_string)
    
    return jsx_string
