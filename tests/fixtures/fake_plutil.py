import plistlib
import sys
from pathlib import Path
from typing import Any


def _resolve(payload: Any, key_path: str) -> Any:
    cursor = payload
    for part in key_path.split("."):
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    return cursor


def _parent_and_key(payload: Any, key_path: str) -> tuple[Any, int | str]:
    parts = key_path.split(".")
    cursor = payload
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    key = int(parts[-1]) if isinstance(cursor, list) else parts[-1]
    return cursor, key


def main(argv: list[str]) -> int:
    command, *args = argv
    destination = Path(args[-1])
    payload = plistlib.loads(destination.read_bytes())
    if command == "-lint":
        print(f"{destination}: OK")
        return 0
    if command == "-extract":
        value = _resolve(payload, args[0])
        print(len(value) if isinstance(value, list) else value)
        return 0

    parent, key = _parent_and_key(payload, args[0])
    if command == "-remove":
        del parent[key]
    elif command == "-insert":
        if isinstance(parent, list):
            parent.insert(key, args[2])
        else:
            if key in parent:
                raise SystemExit(f"cannot insert existing key: {args[0]}")
            parent[key] = args[2]
    elif command == "-replace":
        if isinstance(parent, list):
            # Match macOS Swift plutil: a numeric replace key path inserts.
            parent.insert(key, args[2])
        else:
            if key not in parent:
                raise SystemExit(f"cannot replace missing key: {args[0]}")
            parent[key] = args[2]
    else:
        raise SystemExit(f"unsupported synthetic plutil command: {command}")
    destination.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
