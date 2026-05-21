# Path to your Oh My Zsh installation.
export ZSH="$HOME/.oh-my-zsh"
plugins=(git dnf zoxide zsh-autosuggestions zsh-syntax-highlighting)

source $ZSH/oh-my-zsh.sh
#
# For Java One-liner Compilation
jvc() {
  if [[ -z "$1" ]]; then
    echo "Usage: jvc filename.java"
    return 1
  fi

  filename="$1"
  if [[ ! -f "$filename" ]]; then
    echo "Error: File '$filename' not found."
    return 1
  fi

  # Compile
  javac "$filename"
  if [[ $? -ne 0 ]]; then
    echo "Compilation failed."
    return 1
  fi

  # Extract class name (strip directory and .java extension)
  classname=$(basename "$filename" .java)

  # Run
  java "$classname"
}

# For C++ One-liner Compilation
gpp() {
  if [[ -z "$1" ]]; then
    echo "Usage: gpp filename.cpp"
    return 1
  fi

  filename="$1"
  if [[ ! -f "$filename" ]]; then
    echo "Error: File '$filename' not found."
    return 1
  fi

  # Extract base name (without extension)
  basename=$(basename "$filename" .cpp)
  outfile="/tmp/${basename}_out"

  # Compile
  g++ -std=c++17 "$filename" -o "$outfile"
  if [[ $? -ne 0 ]]; then
    echo "Compilation failed."
    return 1
  fi

  # Run
  "$outfile"
}

# For C One-liner Compilation using Clang
cout() {
  if [[ -z "$1" ]]; then
    echo "Usage: cout filename.c"
    return 1
  fi

  filename="$1"
  if [[ ! -f "$filename" ]]; then
    echo "Error: File '$filename' not found."
    return 1
  fi

  # Extract base name (without extension)
  basename=$(basename "$filename" .c)
  outfile="/tmp/${basename}_out"

  # Compile
  clang -std=c17 "$filename" -o "$outfile"
  if [[ $? -ne 0 ]]; then
    echo "Compilation failed."
    return 1
  fi

  # Run
  "$outfile"
}

alias dev='cd ~/Canvas/'
alias devjava='cd ~/Canvas/Java/'
alias devcpp='cd ~/Canvas/cpp/'
alias restartblue='sudo systemctl restart bluetooth.service'
alias cd-nvim='cd ~/.config/nvim/lua/'


# Cleanup orphaned packages
alias cleanup='sudo pacman -Rns (pacman -Qtdq)'

# Get the error messages from journalctl
alias jctl="journalctl -p 3 -xb"

# Recent installed packages
alias rip="expac --timefmt='%Y-%m-%d %T' '%l\t%n %v' | sort | tail -200 | nl"


# Replace ls with eza
alias ls='eza -al --color=always --group-directories-first --icons' # preferred listing
alias la='eza -a --color=always --group-directories-first --icons'  # all files and dirs
alias ll='eza -l --color=always --group-directories-first --icons'  # long format
alias lt='eza -aT --color=always --group-directories-first --icons' # tree listing
alias l.="eza -a | grep -e '^\.'"                                     # show only dotfiles

# Common use
alias grubup="sudo grub-mkconfig -o /boot/grub/grub.cfg"
alias fixpacman="sudo rm /var/lib/pacman/db.lck"
alias tarnow='tar -acf '
alias untar='tar -zxvf '
alias wget='wget -c '
alias psmem='ps auxf | sort -nr -k 4'
alias psmem10='ps auxf | sort -nr -k 4 | head -10'
alias ..='cd ..'
alias ...='cd ../..'
alias ....='cd ../../..'
alias .....='cd ../../../..'
alias ......='cd ../../../../..'
alias dir='dir --color=auto'
alias vdir='vdir --color=auto'
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'
alias hw='hwinfo --short'                                   # Hardware Info
alias big="expac -H M '%m\t%n' | sort -h | nl"              # Sort installed packages according to size in MB
alias gitpkg='pacman -Q | grep -i "\-git" | wc -l'          # List amount of -git packages
alias update='sudo pacman -Syu'

eval "$(zoxide init --cmd cd zsh)"
