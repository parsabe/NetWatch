# Enable the account-local TeX Live installation in the current shell:
#   source /home/pb70gygu/0/use-texlive.sh
# The cluster's default Perl lacks Time::HiRes, which latexmk requires.
# Use the complete Perl already installed by the cluster alongside TeX Live.

_account_texlive_bin="$HOME/.local/texlive/2026/bin/x86_64-linux"
_account_texlive_perl='/cluster/stages/current/spack-current/opt/spack/linux-rocky8-skylake_avx512/gcc-8.5.0/perl-5.38.0-mupkbgv22vnwaom5oaowopdau2zfelws/bin'

if [ -x "$_account_texlive_bin/pdflatex" ] && [ -x "$_account_texlive_perl/perl" ]; then
    case ":$PATH:" in
        *":$_account_texlive_perl:"*) ;;
        *) PATH="$_account_texlive_perl:$PATH" ;;
    esac
    case ":$PATH:" in
        *":$_account_texlive_bin:"*) ;;
        *) PATH="$_account_texlive_bin:$PATH" ;;
    esac
    export PATH
else
    printf '%s\n' 'TeX Live or its cluster Perl dependency is missing; check the paths in use-texlive.sh.' >&2
fi

unset _account_texlive_bin _account_texlive_perl
