root=/Users/tcardoso/Investigations
master="$root/master-vault"

dests=(
  "$root/bench-ex2-sonnet-med"
  "$root/bench-ex2-haiku"
  "$root/bench-ex2-ds-flash"
  "$root/bench-ex2-ds-flash-think"
  "$root/bench-ex2-ds-pro"
  "$root/bench-ex2-ds-pro-think"
)

for dest in "${dests[@]}"; do
  mkdir -p "$dest/.watchdog/staging" "$dest/.watchdog/queue"
  cp -r "$master/.watchdog/staging/." "$dest/.watchdog/staging/"
  cp "$master/.watchdog/queue/"*.json "$dest/.watchdog/queue/"
  sed -i '' "s#${master}#${dest}#g" "$dest"/.watchdog/queue/*.json
  n=$(ls "$dest/.watchdog/queue/"*.json 2>/dev/null | wc -l | tr -d ' ')
  echo "seeded $dest ($n queued)"
done