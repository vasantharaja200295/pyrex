"""
JSX Parser — turns JSX strings into an AST.

Input:  '<div class="foo"><h1>{title}</h1><button onClick={handler}>Click</button></div>'
Output: Node tree that the transpiler can walk
"""

import re
from dataclasses import dataclass, field
from typing import Union


@dataclass
class TextNode:
    content: str  # raw text or {expression}
    is_expression: bool = False  # True if it's {something}


@dataclass
class JSXNode:
    tag: str
    props: dict = field(default_factory=dict)   # {"class": "foo", "onClick": "handler"}
    children: list = field(default_factory=list) # list of JSXNode | TextNode
    self_closing: bool = False
    component: bool = False  # True if tag starts with uppercase → it's a component ref


def parse_jsx(jsx_string: str) -> JSXNode:
    """
    Parse a JSX string into a node tree.
    Entry point for the parser.
    """
    jsx_string = jsx_string.strip()
    node, _ = _parse_element(jsx_string, 0)
    return node


def _parse_element(src: str, pos: int) -> tuple[JSXNode | TextNode, int]:
    """Recursively parse one element starting at pos."""
    pos = _skip_whitespace(src, pos)

    if pos >= len(src):
        raise SyntaxError("Unexpected end of JSX")

    if src[pos] != '<':
        # Text or expression node
        return _parse_text_or_expr(src, pos)

    # Opening tag
    pos += 1  # skip <

    # Self-closing end tag edge case: </tag> shouldn't be called here
    if pos < len(src) and src[pos] == '/':
        raise SyntaxError(f"Unexpected closing tag at pos {pos}")

    # Parse tag name
    tag, pos = _parse_identifier(src, pos)
    is_component = tag[0].isupper()

    # Parse props
    props, pos = _parse_props(src, pos)

    pos = _skip_whitespace(src, pos)

    # Self closing?
    if src[pos:pos+2] == '/>':
        return JSXNode(tag=tag, props=props, self_closing=True, component=is_component), pos + 2

    # Expect >
    if src[pos] != '>':
        raise SyntaxError(f"Expected '>' at pos {pos}, got '{src[pos]}'")
    pos += 1

    # Parse children until closing tag
    children = []
    while pos < len(src):
        pos = _skip_whitespace(src, pos)
        if pos >= len(src):
            break

        # Check for closing tag
        if src[pos:pos+2] == '</':
            close_start = pos + 2
            close_tag, pos = _parse_identifier(src, close_start)
            # support shorthand </> 
            if close_tag == '':
                close_tag = tag
            pos = _skip_whitespace(src, pos)
            if src[pos] != '>':
                raise SyntaxError(f"Expected '>' to close </{close_tag}>")
            pos += 1
            break

        # Text / expression between tags
        if src[pos] != '<':
            node, pos = _parse_inline_content(src, pos)
            if node:
                children.extend(node)
            continue

        child, pos = _parse_element(src, pos)
        children.append(child)

    return JSXNode(tag=tag, props=props, children=children, component=is_component), pos


def _parse_inline_content(src: str, pos: int) -> tuple[list, int]:
    """Parse text + {expressions} until we hit a tag."""
    nodes = []
    buf = ""
    while pos < len(src) and src[pos] != '<':
        if src[pos] == '{':
            if buf.strip():
                nodes.append(TextNode(content=buf.strip()))
            buf = ""
            expr, pos = _parse_expression(src, pos)
            nodes.append(TextNode(content=expr, is_expression=True))
        else:
            buf += src[pos]
            pos += 1
    if buf.strip():
        nodes.append(TextNode(content=buf.strip()))
    return nodes, pos


def _parse_text_or_expr(src: str, pos: int) -> tuple[TextNode, int]:
    """Parse a plain text or {expression} node."""
    if src[pos] == '{':
        expr, pos = _parse_expression(src, pos)
        return TextNode(content=expr, is_expression=True), pos
    buf = ""
    while pos < len(src) and src[pos] not in ('<', '{'):
        buf += src[pos]
        pos += 1
    return TextNode(content=buf.strip()), pos


def _parse_expression(src: str, pos: int) -> tuple[str, int]:
    """Parse {expression} — handles nested braces."""
    assert src[pos] == '{'
    depth = 0
    buf = ""
    pos += 1  # skip {
    while pos < len(src):
        c = src[pos]
        if c == '{':
            depth += 1
            buf += c
        elif c == '}':
            if depth == 0:
                return buf.strip(), pos + 1
            depth -= 1
            buf += c
        else:
            buf += c
        pos += 1
    raise SyntaxError("Unclosed {expression}")


def _parse_props(src: str, pos: int) -> tuple[dict, int]:
    """Parse props until > or />."""
    props = {}
    while pos < len(src):
        pos = _skip_whitespace(src, pos)
        if src[pos] in ('>', '/'):
            break

        # prop name
        name, pos = _parse_prop_name(src, pos)
        pos = _skip_whitespace(src, pos)

        if pos < len(src) and src[pos] == '=':
            pos += 1  # skip =
            pos = _skip_whitespace(src, pos)

            if src[pos] == '"' or src[pos] == "'":
                # String value
                quote = src[pos]
                pos += 1
                val = ""
                while pos < len(src) and src[pos] != quote:
                    val += src[pos]
                    pos += 1
                pos += 1  # closing quote
                props[name] = val
            elif src[pos] == '{':
                # Expression value
                val, pos = _parse_expression(src, pos)
                props[name] = f"_expr_:{val}"
            else:
                raise SyntaxError(f"Unexpected prop value at pos {pos}")
        else:
            # Boolean prop e.g. <input disabled />
            props[name] = True

    return props, pos


def _parse_identifier(src: str, pos: int) -> tuple[str, int]:
    """Parse a tag or prop name (letters, digits, -, _)."""
    buf = ""
    while pos < len(src) and (src[pos].isalnum() or src[pos] in ('-', '_', '.')):
        buf += src[pos]
        pos += 1
    return buf, pos


def _parse_prop_name(src: str, pos: int) -> tuple[str, int]:
    """Like identifier but also allows : for things like aria-label."""
    buf = ""
    while pos < len(src) and (src[pos].isalnum() or src[pos] in ('-', '_', ':', '.')):
        buf += src[pos]
        pos += 1
    return buf, pos


def _skip_whitespace(src: str, pos: int) -> int:
    while pos < len(src) and src[pos].isspace():
        pos += 1
    return pos
