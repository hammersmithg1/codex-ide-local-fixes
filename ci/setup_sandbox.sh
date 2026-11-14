#!/usr/bin/env bash
set -euo pipefail

# Install the documented Linux prerequisites in the disposable GitHub runner.
# Keep AppArmor enabled and grant user namespaces via Ubuntu's bwrap profile.
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends bubblewrap apparmor-profiles apparmor-utils
if test -f /usr/share/apparmor/extra-profiles/bwrap-userns-restrict; then
  sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict \
    /etc/apparmor.d/bwrap-userns-restrict
fi
if test -f /etc/apparmor.d/bwrap-userns-restrict; then
  sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
fi

# Fail before restoring credentials if the command sandbox cannot start.
bwrap --unshare-user --unshare-net --ro-bind / / -- true
echo 'System bubblewrap can create the required sandbox.'
