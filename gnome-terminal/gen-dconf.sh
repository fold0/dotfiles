#!/bin/bash
# Generates /org/gnome/terminal/ dconf configuration from Xresources-style
# colorscheme files in colors/ subdirectory or passed on command line.
# One profile per color scheme.

set -e -o pipefail

if [[ $# == 0 ]]; then
  SCRIPT_DIR="$(dirname $(readlink -f "$0"))"
  THEMES="$(ls "$SCRIPT_DIR/colors/"*)"
else
  THEMES="$(ls $@)"
fi

DEFAULT_THEME="${DEFAULT_THEME:-Fruidle}"

get_color() {
  cat "$FILENAME" | cpp | egrep ".*[*.]$1:" | egrep -o ':[^!]*' | \
    egrep -o '#[a-zA-Z0-9]{6}'
}

parse_theme() {
  COLOR_FG="$(get_color foreground)"
  COLOR_BG="$(get_color background)"
  PALETTE=""
  for n in {0..15}; do
    PALETTE+="${PALETTE:+, }'$(get_color color$n)'"
  done
}

gen_dconf() {
  NUM=0
  UUID_LIST=""

  for FILENAME in $THEMES; do
    parse_theme

    NAME="$(basename "$FILENAME")"

    NUM=$((NUM+1))
    UUID="00000000-0000-0000-0000-$(printf "%.12X" $NUM)"
    UUID_LIST+="${UUID_LIST:+, }'$UUID'"
    [[ "$FILENAME" == "$DEFAULT_THEME" || -z "$UUID_DEFAULT" ]] &&
      UUID_DEFAULT="$UUID"

    cat <<EOF
[org/gnome/terminal/legacy/profiles:/:${UUID}]
visible-name='$NAME'
background-color='$COLOR_BG'
foreground-color='$COLOR_FG'
use-theme-colors=false
palette=[$PALETTE]
use-system-font=true
default-size-columns=100
default-size-rows=40
scrollback-unlimited=true
scrollbar-policy='never'
audible-bell=false

EOF
  done

  cat <<EOF
[org/gnome/terminal/legacy/profiles:]
list=[$UUID_LIST]
default='$UUID_DEFAULT'

[org/gnome/terminal/legacy/keybindings]
help='disabled'

[org/gnome/terminal/legacy]
menu-accelerator-enabled=false
schema-version=uint32 3
default-show-menubar=false
theme-variant='system'
EOF
}

gen_dconf