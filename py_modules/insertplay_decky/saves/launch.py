"""The shortcut's launch line with the save sync wrapper (docs/remote-library.md#launch-hook).

    exe:      /bin/bash
    options:  "<wrapper>" <key> "<game folder>" -- "/bin/bash" <the card's options>

The card and its manifest stay as they are: only the shortcut's options change.
"""
import os

UNSAFE = ('"', "$", "`", "\\", "\n", "\r")
SHELLS = ("bash", "sh")


def wrap(exe: str, options: str, key: str, root: str, wrapper: str):
    """(exe, options) with the wrapper in front, or None when the line can't be wrapped
    safely (an exe that is not a shell, or a path Steam's quoting can't carry)."""
    if not exe or not os.path.isabs(exe) or os.path.basename(exe) not in SHELLS:
        return None
    if any(c in s for s in (root, wrapper, exe) for c in UNSAFE):
        return None
    return exe, f'"{wrapper}" {key} "{root}" -- "{exe}" {options}'.rstrip()
