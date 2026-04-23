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
alias ls='ls -lAh --color'
alias restartblue='sudo systemctl restart bluetooth.service'
alias cd-nvim='cd ~/.config/nvim/lua/'

eval "$(zoxide init --cmd cd zsh)"
