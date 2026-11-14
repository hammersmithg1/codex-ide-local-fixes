Personal build of the installed official Codex IDE extension with local
workspace-history filtering, UI startup recovery and bootstrap diagnostics.

Disable the official Codex extension before installing this VSIX: both builds
provide the same commands and panels. Install through “Extensions: Install from
VSIX” and reload the VS Code window.

This package has a separate extension ID: hammersmithg1.codex-local-fixes.
It does not receive official Marketplace updates. New builds are published
in this repository's Releases. Original license and notices are retained.

Automated checks cover syntax, generated history behavior, UI recovery logic and
byte-for-byte rollback. They do not replace testing VS Code's actual graphical UI.
