from pathlib import Path


def _scalar(value: str):
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part.strip()) for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip('"').strip("'")


def load_yaml(path: Path) -> dict:
    root = {}
    stack = [(-1, root)]
    last_key_at_indent = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        current = stack[-1][1]
        if line.startswith("- "):
            key = last_key_at_indent.get(indent)
            if key is None:
                raise ValueError(f"List item without parent key in {path}: {line}")
            current = stack[-1][1]
            current.setdefault(key, []).append(_scalar(line[2:]))
            continue
        if ":" not in line:
            raise ValueError(f"Invalid YAML line in {path}: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            current[key] = _scalar(value)
        else:
            current[key] = {}
            stack.append((indent, current[key]))
            last_key_at_indent[indent + 2] = key
        last_key_at_indent[indent] = key
    return root
