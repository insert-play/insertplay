"""Save sync through the remote library (issue #63; contract: docs/remote-library.md#saves).

Standard library only and relative imports only: the plugin copies this package to
`~/.local/share/insertplay/lib/insertplay_saves/`, where the launch wrapper runs it with the
system `python3`, outside Decky and even after the plugin is removed.
"""
