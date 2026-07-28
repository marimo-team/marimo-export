from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass

from marimo._runtime.virtual_file import read_virtual_file
from marimo._utils.data_uri import build_data_url

_VIRTUAL_FILE = re.compile(r"^(?:\./|/)?@file/(?P<size>\d+)-(?P<name>.+)$")
_JS_ALLOWED_URL_PREFIXES = ("data:", "http://", "https://")
_JS_REGEX_PREFIX_KEYWORDS = frozenset(
    {
        "await",
        "case",
        "delete",
        "do",
        "else",
        "in",
        "instanceof",
        "new",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
    }
)
_JS_PUNCTUATORS = (
    ">>>=",
    "===",
    "!==",
    ">>>",
    "**=",
    "&&=",
    "||=",
    "??=",
    "=>",
    "==",
    "!=",
    "<=",
    ">=",
    "++",
    "--",
    "&&",
    "||",
    "??",
    "?.",
    "**",
    "<<",
    ">>",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "...",
)
_CSS_VIRTUAL_FILE_MARKERS = ("./@file/", "/@file/", "@file/")


@dataclass(frozen=True)
class _JavaScriptToken:
    kind: str
    value: str
    escaped: bool = False
    line_break_before: bool = False


@dataclass(frozen=True)
class _CssReference:
    start: int
    end: int
    value: str
    kind: str
    escaped: bool = False


def validate_embedded_esm(source: str) -> None:
    """Validate dependency operands in an embedded AnyWidget module."""

    tokens = _javascript_tokens(source)
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or _is_member_name(tokens, index):
            continue
        if token.value == "import":
            _validate_import(tokens, index)
        elif token.value == "export":
            _validate_export_from(tokens, index)
        elif token.value == "new":
            _validate_new_url(tokens, index)


def _validate_import(tokens: list[_JavaScriptToken], index: int) -> None:
    if index + 1 >= len(tokens):
        return
    following = tokens[index + 1]
    if following.kind == "punctuator" and following.value == ".":
        return
    if following.kind == "punctuator" and following.value == "(":
        if _is_import_method(tokens, index):
            return
        _validate_js_call_operand(tokens, index + 2, "dynamic import")
        return
    if following.kind == "string":
        _validate_js_url(following, "import")
        return
    if following.kind != "identifier" and not (
        following.kind == "punctuator" and following.value in {"*", "{"}
    ):
        return
    from_index = _find_top_level_from(tokens, index + 1)
    if from_index is not None:
        _validate_js_static_operand(tokens, from_index + 1, "import-from")


def _validate_export_from(tokens: list[_JavaScriptToken], index: int) -> None:
    if index + 1 >= len(tokens):
        return
    following = tokens[index + 1]
    if following.kind != "punctuator" or following.value not in {"*", "{"}:
        return
    if following.value == "{":
        closing = _matching_punctuator(tokens, index + 1, "{", "}")
        from_index = closing + 1 if closing is not None else None
        if (
            from_index is None
            or from_index >= len(tokens)
            or tokens[from_index].kind != "identifier"
            or tokens[from_index].value != "from"
        ):
            return
    else:
        from_index = _find_top_level_from(tokens, index + 1)
    if from_index is not None:
        _validate_js_static_operand(tokens, from_index + 1, "export-from")


def _validate_new_url(tokens: list[_JavaScriptToken], index: int) -> None:
    if index + 2 >= len(tokens):
        return
    constructor, opening = tokens[index + 1 : index + 3]
    if not (
        constructor.kind == "identifier"
        and constructor.value == "URL"
        and opening.kind == "punctuator"
        and opening.value == "("
    ):
        return
    operand_index = index + 3
    comma = _top_level_comma(tokens, operand_index)
    if comma is None or not _is_import_meta_url(tokens, comma + 1):
        return
    if comma != operand_index + 1 or tokens[operand_index].kind != "string":
        raise _js_dependency_error("new URL")
    _validate_js_url(tokens[operand_index], "new URL")


def _top_level_comma(tokens: list[_JavaScriptToken], index: int) -> int | None:
    depth = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "punctuator":
            if token.value in {"(", "[", "{"}:
                depth += 1
            elif token.value in {")", "]", "}"}:
                if depth == 0:
                    return None
                depth -= 1
            elif token.value == "," and depth == 0:
                return index
        index += 1
    return None


def _is_import_meta_url(tokens: list[_JavaScriptToken], index: int) -> bool:
    expected = (
        ("identifier", "import"),
        ("punctuator", "."),
        ("identifier", "meta"),
        ("punctuator", "."),
        ("identifier", "url"),
    )
    end = index + len(expected)
    return (
        end < len(tokens)
        and all(
            tokens[index + offset].kind == kind and tokens[index + offset].value == value
            for offset, (kind, value) in enumerate(expected)
        )
        and tokens[end].kind == "punctuator"
        and tokens[end].value in {",", ")"}
    )


def _validate_js_call_operand(tokens: list[_JavaScriptToken], index: int, dependency: str) -> None:
    if index >= len(tokens) or tokens[index].kind != "string":
        raise _js_dependency_error(dependency)
    _validate_js_url(tokens[index], dependency)
    if index + 1 >= len(tokens) or not (
        tokens[index + 1].kind == "punctuator" and tokens[index + 1].value in {",", ")"}
    ):
        raise _js_dependency_error(dependency)


def _validate_js_static_operand(
    tokens: list[_JavaScriptToken], index: int, dependency: str
) -> None:
    if index >= len(tokens) or tokens[index].kind != "string":
        raise _js_dependency_error(dependency)
    _validate_js_url(tokens[index], dependency)


def _validate_js_url(token: _JavaScriptToken, dependency: str) -> None:
    if token.escaped or not token.value.lower().startswith(_JS_ALLOWED_URL_PREFIXES):
        raise _js_dependency_error(dependency, token.value)


def _js_dependency_error(dependency: str, value: str | None = None) -> ValueError:
    suffix = "" if value is None else f"; got {value!r}"
    return ValueError(
        f"AnyWidget embedded ESM {dependency} must use a literal data:, http://, "
        f"or https:// URL{suffix}"
    )


def _find_top_level_from(tokens: list[_JavaScriptToken], index: int) -> int | None:
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "punctuator":
            if token.value == ";" and all(depth == 0 for depth in depths.values()):
                return None
            if token.value in depths:
                depths[token.value] += 1
            elif token.value in closing:
                opener = closing[token.value]
                depths[opener] = max(0, depths[opener] - 1)
        elif (
            token.kind == "identifier"
            and token.value == "from"
            and all(depth == 0 for depth in depths.values())
        ):
            return index
        index += 1
    return None


def _matching_punctuator(
    tokens: list[_JavaScriptToken], index: int, opening: str, closing: str
) -> int | None:
    depth = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "punctuator":
            if token.value == opening:
                depth += 1
            elif token.value == closing:
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return None


def _is_member_name(tokens: list[_JavaScriptToken], index: int) -> bool:
    return (
        index > 0
        and tokens[index - 1].kind == "punctuator"
        and tokens[index - 1].value in {".", "?."}
    )


def _is_import_method(tokens: list[_JavaScriptToken], index: int) -> bool:
    closing = _matching_punctuator(tokens, index + 1, "(", ")")
    if closing is None or closing + 1 >= len(tokens):
        return False
    body = tokens[closing + 1]
    if body.kind != "punctuator" or body.value != "{":
        return False

    # A call expression cannot be followed by a block on the same line. A
    # newline can trigger automatic semicolon insertion, so multiline cases
    # additionally need a class or object-literal member context.
    if not body.line_break_before:
        return True
    container = _enclosing_open_brace(tokens, index)
    return container is not None and _is_member_container(tokens, container)


def _enclosing_open_brace(tokens: list[_JavaScriptToken], index: int) -> int | None:
    depth = 0
    for candidate in range(index - 1, -1, -1):
        token = tokens[candidate]
        if token.kind != "punctuator":
            continue
        if token.value == "}":
            depth += 1
        elif token.value == "{":
            if depth == 0:
                return candidate
            depth -= 1
    return None


def _is_member_container(tokens: list[_JavaScriptToken], opening: int) -> bool:
    if _is_class_body(tokens, opening):
        return True
    if opening == 0:
        return False
    previous = tokens[opening - 1]
    if previous.kind == "identifier":
        return previous.value in {"return", "yield"}
    return previous.kind == "punctuator" and previous.value in {
        "(",
        "[",
        ",",
        ":",
        "=",
        "?",
        "&&",
        "||",
        "??",
    }


def _is_class_body(tokens: list[_JavaScriptToken], opening: int) -> bool:
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    for candidate in range(opening - 1, -1, -1):
        token = tokens[candidate]
        if token.kind == "punctuator":
            if token.value == ")":
                paren_depth += 1
            elif token.value == "(":
                if paren_depth == 0:
                    return False
                paren_depth -= 1
            elif token.value == "]":
                bracket_depth += 1
            elif token.value == "[":
                if bracket_depth == 0:
                    return False
                bracket_depth -= 1
            elif token.value == "}":
                brace_depth += 1
            elif token.value == "{":
                if brace_depth == 0:
                    return False
                brace_depth -= 1
            elif (
                token.value == ";" and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0
            ):
                return False
        elif (
            token.kind == "identifier"
            and token.value == "class"
            and paren_depth == 0
            and bracket_depth == 0
            and brace_depth == 0
        ):
            return True
    return False


def _javascript_tokens(source: str) -> list[_JavaScriptToken]:
    tokens: list[_JavaScriptToken] = []
    index = 0
    line_break_before = False
    while index < len(source):
        char = source[index]
        if char.isspace():
            line_break_before = line_break_before or char in "\r\n\u2028\u2029"
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            line_break_before = line_break_before or newline >= 0
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("AnyWidget embedded ESM contains an unterminated comment")
            line_break_before = line_break_before or any(
                marker in source[index : end + 2] for marker in ("\r", "\n", "\u2028", "\u2029")
            )
            index = end + 2
            continue
        if char in {'"', "'"}:
            index, value, escaped = _consume_javascript_string(source, index, char)
            tokens.append(
                _JavaScriptToken("string", value, escaped, line_break_before=line_break_before)
            )
            line_break_before = False
            continue
        if char == "`":
            index, expressions = _consume_javascript_template(source, index)
            tokens.append(_JavaScriptToken("template", "", line_break_before=line_break_before))
            tokens.extend(expressions)
            line_break_before = False
            continue
        if char == "/" and _javascript_regex_can_start(tokens):
            regex_end = _consume_javascript_regex(source, index)
            if regex_end is not None:
                index = regex_end
                tokens.append(_JavaScriptToken("regex", "", line_break_before=line_break_before))
                line_break_before = False
                continue
        if _javascript_identifier_start(char) or char == "\\":
            end, value, escaped = _consume_javascript_identifier(source, index)
            tokens.append(
                _JavaScriptToken("identifier", value, escaped, line_break_before=line_break_before)
            )
            index = end
            line_break_before = False
            continue
        if char.isdigit():
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] in "._"):
                end += 1
            tokens.append(
                _JavaScriptToken("number", source[index:end], line_break_before=line_break_before)
            )
            index = end
            line_break_before = False
            continue
        punctuator = next(
            (item for item in _JS_PUNCTUATORS if source.startswith(item, index)), char
        )
        tokens.append(
            _JavaScriptToken("punctuator", punctuator, line_break_before=line_break_before)
        )
        index += len(punctuator)
        line_break_before = False
    return tokens


def _consume_javascript_string(source: str, index: int, quote: str) -> tuple[int, str, bool]:
    start = index + 1
    index = start
    escaped = False
    while index < len(source):
        char = source[index]
        if char == quote:
            return index + 1, source[start:index], escaped
        if char == "\\":
            escaped = True
            index += 2
            continue
        if char in "\r\n\u2028\u2029":
            raise ValueError("AnyWidget embedded ESM contains an unterminated string")
        index += 1
    raise ValueError("AnyWidget embedded ESM contains an unterminated string")


def _consume_javascript_identifier(source: str, index: int) -> tuple[int, str, bool]:
    value: list[str] = []
    escaped = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index, char = _consume_javascript_identifier_escape(source, index)
            escaped = True
        elif _javascript_identifier_part(char):
            index += 1
        else:
            break
        valid = (
            _javascript_identifier_start(char) if not value else _javascript_identifier_part(char)
        )
        if not valid:
            raise ValueError("AnyWidget embedded ESM contains an invalid identifier escape")
        value.append(char)
    if not value:
        raise ValueError("AnyWidget embedded ESM contains an invalid identifier")
    return index, "".join(value), escaped


def _consume_javascript_identifier_escape(source: str, index: int) -> tuple[int, str]:
    if not source.startswith("\\u", index):
        raise ValueError("AnyWidget embedded ESM contains an invalid identifier escape")
    index += 2
    if index < len(source) and source[index] == "{":
        end = source.find("}", index + 1)
        digits = source[index + 1 : end] if end >= 0 else ""
        if not 1 <= len(digits) <= 6 or any(
            char not in "0123456789abcdefABCDEF" for char in digits
        ):
            raise ValueError("AnyWidget embedded ESM contains an invalid identifier escape")
        index = end + 1
    else:
        digits = source[index : index + 4]
        if len(digits) != 4 or any(char not in "0123456789abcdefABCDEF" for char in digits):
            raise ValueError("AnyWidget embedded ESM contains an invalid identifier escape")
        index += 4
    codepoint = int(digits, 16)
    if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
        raise ValueError("AnyWidget embedded ESM contains an invalid identifier escape")
    return index, chr(codepoint)


def _consume_javascript_template(source: str, index: int) -> tuple[int, list[_JavaScriptToken]]:
    expressions: list[_JavaScriptToken] = []
    index += 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source.startswith("${", index):
            end = _javascript_template_expression_end(source, index + 2)
            expressions.extend(_javascript_tokens(source[index + 2 : end]))
            index = end + 1
        elif source[index] == "`":
            return index + 1, expressions
        else:
            index += 1
    raise ValueError("AnyWidget embedded ESM contains an unterminated template literal")


def _javascript_template_expression_end(source: str, index: int) -> int:
    expression_start = index
    depth = 1
    while index < len(source):
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("AnyWidget embedded ESM contains an unterminated comment")
            index = end + 2
            continue
        if source[index] in {'"', "'"}:
            index, _, _ = _consume_javascript_string(source, index, source[index])
            continue
        if source[index] == "`":
            index, _ = _consume_javascript_template(source, index)
            continue
        if source[index] == "/":
            prefix_tokens = _javascript_tokens(source[expression_start:index])
            if _javascript_regex_can_start(prefix_tokens):
                regex_end = _consume_javascript_regex(source, index)
                if regex_end is not None:
                    index = regex_end
                    continue
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("AnyWidget embedded ESM contains an unterminated template expression")


def _consume_javascript_regex(source: str, index: int) -> int | None:
    index += 1
    in_character_class = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char in "\r\n\u2028\u2029":
            return None
        if char == "[":
            in_character_class = True
        elif char == "]":
            in_character_class = False
        elif char == "/" and not in_character_class:
            index += 1
            while index < len(source) and _javascript_identifier_part(source[index]):
                index += 1
            return index
        index += 1
    return None


def _javascript_regex_can_start(tokens: list[_JavaScriptToken]) -> bool:
    if not tokens:
        return True
    previous = tokens[-1]
    if previous.kind == "identifier":
        return previous.value in _JS_REGEX_PREFIX_KEYWORDS
    if previous.kind in {"number", "regex", "string", "template"}:
        return False
    if previous.value == ")":
        opening = _matching_open_paren(tokens, len(tokens) - 1)
        return (
            opening is not None
            and opening > 0
            and tokens[opening - 1].kind == "identifier"
            and tokens[opening - 1].value in {"catch", "for", "if", "switch", "while", "with"}
        )
    if previous.value == "}":
        opening = _matching_open_brace(tokens, len(tokens) - 1)
        if opening is not None and opening > 0 and tokens[opening - 1].value == ")":
            condition = _matching_open_paren(tokens, opening - 1)
            return (
                condition is not None
                and condition > 0
                and tokens[condition - 1].kind == "identifier"
                and tokens[condition - 1].value in {"catch", "for", "if", "switch", "while", "with"}
            )
    return previous.value not in {"]", "}", "++", "--"}


def _matching_open_paren(tokens: list[_JavaScriptToken], closing: int) -> int | None:
    depth = 0
    for index in range(closing, -1, -1):
        token = tokens[index]
        if token.kind != "punctuator":
            continue
        if token.value == ")":
            depth += 1
        elif token.value == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _matching_open_brace(tokens: list[_JavaScriptToken], closing: int) -> int | None:
    depth = 0
    for index in range(closing, -1, -1):
        token = tokens[index]
        if token.kind != "punctuator":
            continue
        if token.value == "}":
            depth += 1
        elif token.value == "{":
            depth -= 1
            if depth == 0:
                return index
    return None


def _javascript_identifier_start(char: str) -> bool:
    return char in "_$" or char.isalpha() or ord(char) >= 128


def _javascript_identifier_part(char: str) -> bool:
    return _javascript_identifier_start(char) or char.isdigit()


def portable_css(source: str) -> str:
    """Inline marimo virtual assets and validate remaining CSS references."""

    replacements: list[tuple[int, int, str]] = []
    for reference in _css_references(source):
        replacement = _portable_css_reference(reference)
        if replacement is not None:
            replacements.append((reference.start, reference.end, replacement))
    for start, end, replacement in reversed(replacements):
        source = f"{source[:start]}{replacement}{source[end:]}"
    return source


def _css_references(source: str) -> list[_CssReference]:
    references: list[_CssReference] = []
    index = 0
    while index < len(source):
        if source.startswith("/*", index):
            index = _consume_css_comment(source, index)
            continue
        if source[index] in {'"', "'"}:
            index, _, _ = _consume_css_string(source, index, source[index])
            continue
        if source[index] == "@":
            end, identifier = _consume_css_identifier(source, index + 1)
            if identifier.lower() == "import":
                reference, index = _css_import_reference(source, end)
                references.append(reference)
                continue
            index = max(end, index + 1)
            continue
        if _css_identifier_start(source[index]):
            end, identifier = _consume_css_identifier(source, index)
            if identifier.lower() == "url":
                opening = _skip_css_trivia(source, end)
                if opening < len(source) and source[opening] == "(":
                    reference, index = _css_url_reference(source, opening, "url()")
                    references.append(reference)
                    continue
            index = end
            continue
        index += 1
    return references


def _css_import_reference(source: str, index: int) -> tuple[_CssReference, int]:
    index = _skip_css_trivia(source, index)
    if index >= len(source):
        raise ValueError("AnyWidget CSS @import must use a literal URL")
    if source[index] in {'"', "'"}:
        end, value, escaped = _consume_css_string(source, index, source[index])
        return _CssReference(index + 1, end - 1, value, "@import", escaped), end
    identifier_end, identifier = _consume_css_identifier(source, index)
    if identifier.lower() == "url":
        opening = _skip_css_trivia(source, identifier_end)
        if opening < len(source) and source[opening] == "(":
            return _css_url_reference(source, opening, "@import")
    raise ValueError("AnyWidget CSS @import must use a literal URL")


def _css_url_reference(source: str, opening: int, kind: str) -> tuple[_CssReference, int]:
    index = _skip_css_trivia(source, opening + 1)
    if index >= len(source):
        raise ValueError(f"AnyWidget CSS {kind} is unterminated")
    if source[index] in {'"', "'"}:
        end, value, escaped = _consume_css_string(source, index, source[index])
        closing = _skip_css_trivia(source, end)
        if closing >= len(source) or source[closing] != ")":
            raise ValueError(f"AnyWidget CSS {kind} is malformed")
        return _CssReference(index + 1, end - 1, value, kind, escaped), closing + 1
    start = index
    while index < len(source) and source[index] != ")":
        if source.startswith("/*", index) or source[index] in {'"', "'", "("}:
            raise ValueError(f"AnyWidget CSS {kind} must use a literal URL")
        index += 1
    if index >= len(source):
        raise ValueError(f"AnyWidget CSS {kind} is unterminated")
    end = index
    while end > start and source[end - 1].isspace():
        end -= 1
    value = source[start:end]
    return _CssReference(start, end, value, kind, "\\" in value), index + 1


def _portable_css_reference(reference: _CssReference) -> str | None:
    url = reference.value.strip()
    lowered = url.lower()
    if reference.escaped:
        raise ValueError(
            f"AnyWidget CSS {reference.kind} uses an escaped asset URL, which is unsupported"
        )
    if not url:
        raise ValueError(f"AnyWidget CSS {reference.kind} must use a non-empty URL")
    if url.startswith("#"):
        return None
    if lowered.startswith(("data:", "http://", "https://")):
        return None
    if any(url.startswith(marker) for marker in _CSS_VIRTUAL_FILE_MARKERS):
        if reference.kind == "@import":
            raise ValueError("AnyWidget CSS @import virtual files must be bundled before export")
        return _inline_css_virtual_file(url)
    if url.startswith("/") and not url.startswith("//"):
        return None
    raise ValueError(f"AnyWidget CSS {reference.kind} uses an unsupported asset URL: {url!r}")


def _inline_css_virtual_file(url: str) -> str:
    match = _VIRTUAL_FILE.fullmatch(url)
    if match is None:
        raise ValueError(f"AnyWidget CSS contains a malformed marimo virtual file URL: {url!r}")
    expected_size = int(match.group("size"))
    try:
        contents = (
            b"" if expected_size == 0 else read_virtual_file(match.group("name"), expected_size)
        )
    except Exception as error:
        raise ValueError(f"AnyWidget CSS could not read marimo virtual file {url!r}") from error
    if len(contents) != expected_size:
        raise ValueError(
            f"AnyWidget CSS {url!r} declared {expected_size} bytes but returned {len(contents)}"
        )
    media_type = mimetypes.guess_type(match.group("name"))[0] or "application/octet-stream"
    return build_data_url(media_type, base64.b64encode(contents))


def _skip_css_trivia(source: str, index: int) -> int:
    while index < len(source):
        if source[index].isspace():
            index += 1
        elif source.startswith("/*", index):
            index = _consume_css_comment(source, index)
        else:
            break
    return index


def _consume_css_comment(source: str, index: int) -> int:
    end = source.find("*/", index + 2)
    if end < 0:
        raise ValueError("AnyWidget CSS contains an unterminated comment")
    return end + 2


def _consume_css_string(source: str, index: int, quote: str) -> tuple[int, str, bool]:
    start = index + 1
    index = start
    escaped = False
    while index < len(source):
        if source[index] == quote:
            return index + 1, source[start:index], escaped
        if source[index] == "\\":
            escaped = True
            index += 2
        else:
            index += 1
    raise ValueError("AnyWidget CSS contains an unterminated string")


def _consume_css_identifier(source: str, index: int) -> tuple[int, str]:
    value: list[str] = []
    while index < len(source):
        char = source[index]
        if char == "\\":
            index, decoded = _consume_css_escape(source, index)
            value.append(decoded)
            continue
        if _css_identifier_part(char):
            value.append(char)
            index += 1
            continue
        break
    return index, "".join(value)


def _consume_css_escape(source: str, index: int) -> tuple[int, str]:
    index += 1
    if index >= len(source) or source[index] in "\r\n\f":
        raise ValueError("AnyWidget CSS contains an invalid escape")
    start = index
    while index < len(source) and index - start < 6 and source[index] in "0123456789abcdefABCDEF":
        index += 1
    if index > start:
        codepoint = int(source[start:index], 16)
        if index < len(source) and source[index].isspace():
            index += 1
        if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return index, "\ufffd"
        return index, chr(codepoint)
    return index + 1, source[index]


def _css_identifier_start(char: str) -> bool:
    return char in "_-\\" or char.isalpha() or ord(char) >= 128


def _css_identifier_part(char: str) -> bool:
    return _css_identifier_start(char) or char.isdigit()
