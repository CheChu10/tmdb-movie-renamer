import re
from pathlib import Path
from typing import Dict, List, Set, Tuple


TEMPLATE_FILTER_DESCRIPTIONS: Dict[str, str] = {
    'upper': 'Transform value to uppercase.',
    'lower': 'Transform value to lowercase.',
    'title': 'Title-case the value.',
    'capitalize': 'Capitalize only the first character.',
    'initials': 'Take first character of each word.',
    'char:N': 'Take character at index N (supports negative indexes).',
    'slice:START:END': 'Slice value like Python (START/END optional).',
    'stem': 'Remove final file extension segment (path stem).',
    'fallback:ARG': 'If empty, use ARG as literal text; use ${FIELD} for variable fallback.',
    'replace:OLD:NEW': 'Replace substring OLD with NEW.',
    'trim': 'Trim leading/trailing spaces.',
    'ifexists:THEN[:ELSE]': 'Rule: if current value exists, render THEN, else ELSE.',
    'ifcontains:NEEDLE:THEN[:ELSE]': 'Rule: if current value contains NEEDLE.',
    'ifeq:TEXT:THEN[:ELSE]': 'Rule: if current value equals TEXT.',
    'ifgt:NUMBER:THEN[:ELSE]': 'Rule: if current numeric value > NUMBER.',
    'ifge:NUMBER:THEN[:ELSE]': 'Rule: if current numeric value >= NUMBER.',
    'iflt:NUMBER:THEN[:ELSE]': 'Rule: if current numeric value < NUMBER.',
    'ifle:NUMBER:THEN[:ELSE]': 'Rule: if current numeric value <= NUMBER.',
}

FILTER_NAME_ALIASES = {
    'upper': 'upper',
    'lower': 'lower',
    'title': 'title',
    'capitalize': 'capitalize',
    'initials': 'initials',
    'char': 'char',
    'slice': 'slice',
    'stem': 'stem',
    'fallback': 'fallback',
    'replace': 'replace',
    'trim': 'trim',
    'strip': 'trim',
    'ifexists': 'ifexists',
    'ifcontains': 'ifcontains',
    'ifeq': 'ifeq',
    'ifgt': 'ifgt',
    'ifge': 'ifge',
    'iflt': 'iflt',
    'ifle': 'ifle',
}

TEMPLATE_FIELD_TOKEN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)(?:\[\s*(-?\d+)\s*\])?\s*$')
TEMPLATE_RULE_VAR_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*(?:\[\s*-?\d+\s*\])?)\}')
TEMPLATE_VALUE_RE = re.compile(r'%\s*value\s*%', flags=re.IGNORECASE)
TEMPLATE_FORBIDDEN_VALUE_VAR_RE = re.compile(r'\$\{\s*VALUE\s*\}', flags=re.IGNORECASE)
TEMPLATE_LEGACY_RULE_FIELD_RE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*(?:\[\s*-?\d+\s*\])?)')
TEMPLATE_FALLBACK_VAR_RE = re.compile(r'^\$\{(.+)\}$')


def _iter_template_segments(template: str) -> List[Tuple[bool, str]]:
    segments: List[Tuple[bool, str]] = []
    text_buffer: List[str] = []
    in_expr = False
    expr_start = 0
    depth = 0

    for idx, ch in enumerate(template):
        if not in_expr:
            if ch == '{':
                if text_buffer:
                    segments.append((False, ''.join(text_buffer)))
                    text_buffer = []
                in_expr = True
                expr_start = idx + 1
                depth = 1
            else:
                text_buffer.append(ch)
            continue

        if ch == '{':
            depth += 1
            continue
        if ch == '}':
            depth -= 1
            if depth == 0:
                segments.append((True, template[expr_start:idx]))
                in_expr = False
                continue

    if in_expr:
        raise ValueError('Destination template has unbalanced braces.')

    if text_buffer:
        segments.append((False, ''.join(text_buffer)))

    return segments


def normalize_template_field_name(field_name: str) -> str:
    return (field_name or '').strip().upper()


def _to_float(value: str) -> float:
    text = (value or '').strip()
    if not text:
        raise ValueError('empty numeric value')

    frac = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if frac:
        den = int(frac.group(2))
        if den == 0:
            raise ValueError('division by zero')
        return int(frac.group(1)) / den

    num = re.search(r'\d+(?:[\.,]\d+)?', text)
    if not num:
        raise ValueError('no numeric token')
    return float(num.group(0).replace(',', '.'))


def _split_template_expression(expression: str) -> Tuple[str, List[str]]:
    raw_pipe_parts = (expression or '').split('|')
    if not raw_pipe_parts:
        raise ValueError('Template expression cannot be empty.')

    base_part = raw_pipe_parts[0].strip()
    if not base_part:
        raise ValueError('Template expression cannot be empty.')

    dot_parts = [part.strip() for part in base_part.split('.') if part.strip()]
    if not dot_parts:
        raise ValueError('Template expression cannot be empty.')

    field_token = dot_parts[0]
    filters = dot_parts[1:]
    for raw_filter in raw_pipe_parts[1:]:
        filter_token = raw_filter.lstrip()
        if not filter_token:
            raise ValueError('Template filter cannot be empty.')
        filters.append(filter_token)

    return field_token, filters


def _resolve_template_field_token(field_token: str, values: Dict[str, str], allowed_fields: Set[str]) -> str:
    match = TEMPLATE_FIELD_TOKEN_RE.match(field_token or '')
    if not match:
        raise ValueError(f"Invalid template field token '{field_token}'.")

    raw_field_name = match.group(1)
    field_name = normalize_template_field_name(raw_field_name)
    if field_name not in allowed_fields:
        raise ValueError(f"Unknown template field '{raw_field_name}'.")

    base_value = str(values.get(field_name, '') or '')
    index_raw = match.group(2)
    if index_raw is None:
        return base_value

    try:
        index = int(index_raw)
    except Exception:
        raise ValueError(f"Invalid index '{index_raw}' in field token '{field_token}'.")

    if not base_value:
        return ''

    try:
        return base_value[index]
    except Exception:
        return ''


def _normalize_filter_name(raw_name: str) -> str:
    canonical = FILTER_NAME_ALIASES.get((raw_name or '').strip().lower())
    if not canonical:
        raise ValueError(f"Unknown template filter '{raw_name}'.")
    return canonical


def _parse_filter_token(filter_token: str) -> Tuple[str, List[str]]:
    token = filter_token or ''
    if not token.strip():
        raise ValueError('Template filter cannot be empty.')

    parts = token.split(':')
    if not parts or not parts[0]:
        raise ValueError('Template filter cannot be empty.')

    filter_name = _normalize_filter_name(parts[0].strip())
    args = parts[1:]
    return filter_name, args


def _render_rule_text(text: str, current: str, values: Dict[str, str], allowed_fields: Set[str]) -> str:
    raw = text or ''

    _validate_rule_text_syntax(raw)

    with_value = TEMPLATE_VALUE_RE.sub(current or '', raw)

    rule_values = dict(values)
    rule_allowed_fields = set(allowed_fields)

    def _replace_field(match: re.Match) -> str:
        field_token = match.group(1) or ''
        try:
            return _resolve_template_field_token(field_token, rule_values, rule_allowed_fields)
        except Exception:
            return ''

    return TEMPLATE_RULE_VAR_RE.sub(_replace_field, with_value)


def _validate_rule_text_syntax(raw: str) -> None:
    text = raw or ''

    if TEMPLATE_FORBIDDEN_VALUE_VAR_RE.search(text):
        raise ValueError("Use '%value%' for current conditional value instead of '${VALUE}'.")

    if TEMPLATE_LEGACY_RULE_FIELD_RE.search(text):
        raise ValueError("Use '${FIELD}' syntax inside rule text instead of legacy '$FIELD'.")


def _evaluate_condition_rule(
    passed: bool,
    args: List[str],
    *,
    current: str,
    rule_name: str,
    min_args: int = 1,
    then_idx: int = 0,
    else_idx: int = 1,
    values: Dict[str, str],
    allowed_fields: Set[str],
) -> str:
    if len(args) < min_args:
        raise ValueError(f"Filter '{rule_name}' expects at least {min_args} argument(s).")

    then_text = args[then_idx] if len(args) > then_idx else ''
    else_text = ':'.join(args[else_idx:]) if len(args) > else_idx else ''

    _validate_rule_text_syntax(then_text)
    _validate_rule_text_syntax(else_text)

    chosen = then_text if passed else else_text
    if not chosen:
        return ''

    return _render_rule_text(chosen, current, values, allowed_fields)


def _apply_template_filter(value: str, filter_token: str, values: Dict[str, str], allowed_fields: Set[str]) -> str:
    filter_name, args = _parse_filter_token(filter_token)
    current = value or ''

    if filter_name == 'upper':
        return current.upper()

    if filter_name == 'lower':
        return current.lower()

    if filter_name == 'title':
        return current.title()

    if filter_name == 'capitalize':
        return current.capitalize()

    if filter_name == 'initials':
        initials = re.findall(r'(?u)\b\w', current)
        return ''.join(initials)

    if filter_name == 'char':
        if len(args) != 1 or not args[0]:
            raise ValueError("Filter 'char' expects exactly one integer argument.")
        try:
            idx = int(args[0])
        except Exception:
            raise ValueError(f"Filter 'char' received invalid index '{args[0]}'.")

        if not current:
            return ''

        try:
            return current[idx]
        except Exception:
            return ''

    if filter_name == 'slice':
        if len(args) == 0 or len(args) > 2:
            raise ValueError("Filter 'slice' expects one or two integer arguments.")

        try:
            start = int(args[0]) if args[0] else None
            end = int(args[1]) if len(args) == 2 and args[1] else None
        except Exception:
            raise ValueError("Filter 'slice' received a non-integer index.")

        return current[slice(start, end)]

    if filter_name == 'stem':
        if args:
            raise ValueError("Filter 'stem' does not take arguments.")
        return Path(current).stem if current else ''

    if filter_name == 'fallback':
        if len(args) < 1:
            raise ValueError("Filter 'fallback' expects an argument.")
        if current:
            return current

        fallback_raw = ':'.join(args).strip()
        if not fallback_raw:
            return ''

        var_match = TEMPLATE_FALLBACK_VAR_RE.match(fallback_raw)
        if var_match:
            nested_expression = (var_match.group(1) or '').strip()
            if not nested_expression:
                return ''
            return _evaluate_template_expression(nested_expression, values, allowed_fields)

        return fallback_raw

    if filter_name == 'ifexists':
        return _evaluate_condition_rule(
            bool(current),
            args,
            current=current,
            rule_name='ifexists',
            values=values,
            allowed_fields=allowed_fields,
        )

    if filter_name == 'ifcontains':
        if len(args) < 2:
            raise ValueError("Filter 'ifcontains' expects NEEDLE and THEN arguments.")
        needle = args[0]
        passed = needle.lower() in current.lower() if needle else False
        return _evaluate_condition_rule(
            passed,
            args,
            current=current,
            rule_name='ifcontains',
            min_args=2,
            then_idx=1,
            else_idx=2,
            values=values,
            allowed_fields=allowed_fields,
        )

    if filter_name == 'ifeq':
        if len(args) < 2:
            raise ValueError("Filter 'ifeq' expects VALUE and THEN arguments.")
        expected = args[0]
        passed = current == expected
        return _evaluate_condition_rule(
            passed,
            args,
            current=current,
            rule_name='ifeq',
            min_args=2,
            then_idx=1,
            else_idx=2,
            values=values,
            allowed_fields=allowed_fields,
        )

    if filter_name in {'ifgt', 'ifge', 'iflt', 'ifle'}:
        if len(args) < 2:
            raise ValueError(f"Filter '{filter_name}' expects NUMBER and THEN arguments.")

        try:
            current_num = _to_float(current)
            threshold = _to_float(args[0])
        except Exception:
            current_num = None
            threshold = None

        if current_num is None or threshold is None:
            passed = False
        elif filter_name == 'ifgt':
            passed = current_num > threshold
        elif filter_name == 'ifge':
            passed = current_num >= threshold
        elif filter_name == 'iflt':
            passed = current_num < threshold
        else:
            passed = current_num <= threshold

        return _evaluate_condition_rule(
            passed,
            args,
            current=current,
            rule_name=filter_name,
            min_args=2,
            then_idx=1,
            else_idx=2,
            values=values,
            allowed_fields=allowed_fields,
        )

    if filter_name == 'replace':
        if len(args) < 2:
            raise ValueError("Filter 'replace' expects OLD and NEW arguments.")
        old = args[0]
        new = ':'.join(args[1:])
        return current.replace(old, new)

    if filter_name == 'trim':
        if args:
            raise ValueError("Filter 'trim' does not take arguments.")
        return current.strip()

    raise ValueError(f"Unsupported template filter '{filter_name}'.")


def _evaluate_template_expression(expression: str, values: Dict[str, str], allowed_fields: Set[str]) -> str:
    field_token, filters = _split_template_expression(expression)
    value = _resolve_template_field_token(field_token, values, allowed_fields)
    for filter_token in filters:
        value = _apply_template_filter(value, filter_token, values, allowed_fields)
    return value


def validate_template(template: str, allowed_fields: Set[str]) -> None:
    candidate = (template or '').strip()
    if not candidate:
        raise ValueError('Destination template cannot be empty.')

    segments = _iter_template_segments(candidate)
    dummy_values = {field: '' for field in allowed_fields}
    for is_expr, expression in segments:
        if not is_expr:
            continue
        if not expression.strip():
            raise ValueError('Destination template has an empty placeholder {}.')
        try:
            _evaluate_template_expression(expression, dummy_values, allowed_fields)
        except ValueError as e:
            raise ValueError(f"In placeholder '{{{expression.strip()}}}': {e}")


def render_template(template: str, values: Dict[str, str], allowed_fields: Set[str]) -> str:
    normalized_values = {
        normalize_template_field_name(key): str(value or '')
        for key, value in values.items()
    }

    rendered: List[str] = []
    for is_expr, content in _iter_template_segments(template):
        if not is_expr:
            rendered.append(content)
            continue

        expression = content
        if not expression.strip():
            raise ValueError('Destination template has an empty placeholder {}.')
        try:
            rendered.append(_evaluate_template_expression(expression, normalized_values, allowed_fields))
        except ValueError as e:
            raise ValueError(f"In placeholder '{{{expression.strip()}}}': {e}")

    return ''.join(rendered)
