Personal build of the installed official Codex IDE extension with local
workspace-history filtering, UI startup recovery, bootstrap diagnostics and
an in-extension GitHub update checker.

Disable the official Codex extension before installing this VSIX: both builds
provide the same commands and panels. Install through “Extensions: Install from
VSIX” and reload the VS Code window.

This package has a separate extension ID: hammersmithg1.codex-local-fixes.
It does not receive official Marketplace updates. The built-in checker offers
new builds from this repository's Releases; installation requires clicking Update.
Use “Codex: Check for Updates (Local Fixes)” for an immediate check. Automatic
checks run at most every six hours and can be disabled in settings. No OS service
is installed. Reloading the window remains your choice.
Original license and notices are retained.

Automated checks cover syntax, generated history behavior, UI recovery logic and
byte-for-byte rollback. They do not replace testing VS Code's actual graphical UI.
