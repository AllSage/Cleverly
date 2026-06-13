#compdef cleverly cleverly-backup cleverly-calendar cleverly-contacts cleverly-cookbook cleverly-docs cleverly-gallery cleverly-mail cleverly-mcp cleverly-memory cleverly-notes cleverly-personal cleverly-preset cleverly-research cleverly-sessions cleverly-signature cleverly-skills cleverly-tasks cleverly-theme cleverly-webhook
# Zsh tab-completion for the cleverly umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/cleverly-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `cleverly <tab>` completes subcommands; `cleverly mail <tab>`
# completes mail subcommands; `cleverly-mail <tab>` works the same.

_cleverly_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _cleverly_subs

_cleverly_refresh() {
    _cleverly_subs=()
    local dir="$(_cleverly_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/cleverly-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#cleverly-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _cleverly_subs[$sub]="$commands"
    done
}

_cleverly() {
    [[ ${#_cleverly_subs} -eq 0 ]] && _cleverly_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "cleverly" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_cleverly_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_cleverly_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_cleverly_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # cleverly-foo <tab>
    local sub="${cmd#cleverly-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_cleverly_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_cleverly "$@"
