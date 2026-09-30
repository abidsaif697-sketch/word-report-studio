#!/usr/bin/env bash
# Download the Stitch photography into img/ and repoint index.html at the local
# copies. Run it once from any machine with normal internet access:
#
#     ./fetch-assets.sh
#
# Safe to re-run: it re-downloads, and rewrites index.html only on a clean fetch.
# The source URLs are Google-signed and expire, so do this before they lapse.

set -euo pipefail
cd "$(dirname "$0")"

OUT=img
PAGE=index.html
mkdir -p "$OUT"

# key|url — keys match the IMG map in index.html
ASSETS=$(cat <<'LIST'
bay|https://lh3.googleusercontent.com/aida-public/AB6AXuAzaau06YKegVqXi6UQPuL5gmsGifrC9BR9O3iswCKY-kFWOeWouWNafE2_ImQFgOhZ01o9m33ShPMKg2HTkaJTQHW54aZazXnETdRGcXG_zzrWTfUJR3WYe1sY0DITtWnMZfoR0q6V_ie01-x
lc300|https://lh3.googleusercontent.com/aida-public/AB6AXuB1xjcjyPw6_xOIQyA1PDLp77LldIwPgLQNVVirgdNYY8OwFfErKWTvc1EGdLyLhez0lMddJr9F5heIag_LPGepQzhuVEGCf0kIwO8WIC01rD679A6w7M3RMupZaAcP8tVZ6sl2AEeNDslMNo_
fleet|https://lh3.googleusercontent.com/aida-public/AB6AXuBGmcdty3N2QIGrfcxWvKyOtVbKM46DKaJY4OQxFjyszGdmCrt-RRqtjU0wgFHC3N1EoVPE3vcKYSssfVHP_-Hj45-uzHxfRxb7g7TaAUud305el2aMuP96MXUhvmfLzCEmRwTxo_yOA5xI2cX
exec|https://lh3.googleusercontent.com/aida-public/AB6AXuC6MQ5IV5UqsZpmfw-wcyCTUMMml-WXkU2UxbIW0Vw-FQJ4YtXaNSAB86zMawVOBlOkW4bq8x5UqFET1qERDKG83AfqcstaXD_9TKhcTcH-hI9xRR5MDGtNvPeQv2q8XctZ2DyZi1hJ3BeNPJf
patrol|https://lh3.googleusercontent.com/aida-public/AB6AXuDMlbz8xh9nyKCXutrVsuHXcnDF2UFPHFrQVQkeMJCsNo5-h3f8Zn9HygFBdLjE-a2IQEO3rRP1v_PwhmhZaeqzXWzc86RxKIZNXMRM1vQ0n0NK3Rh5iV64Ojwyv-_VtQ8lb9aQOOpjqlgRWWD
lx600|https://lh3.googleusercontent.com/aida-public/AB6AXuDZqFFl3_54MWaPE9l8ZE3CmsdGbsLOB7G6Q98if9tWbtPxAm9TdjpFkL0pLZqEl1gZO85CYgDjkLc55ay_51iEvALx4rHbtuXzAkv8kleCk_VZDlddVHeXEILx9Pto7nIbcUbBvm5VxjpKJFE
LIST
)

fail=0
while IFS='|' read -r key url; do
  [ -n "$key" ] || continue
  tmp=$(mktemp)
  printf '  %-7s ' "$key"

  if ! curl -fsSL --max-time 90 --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
    echo "FAILED (network or expired URL)"
    rm -f "$tmp"; fail=1; continue
  fi

  # Name the file by what it actually is, not by what the URL claimed.
  mime=$(file -b --mime-type "$tmp")
  case "$mime" in
    image/jpeg) ext=jpg ;;
    image/png)  ext=png ;;
    image/webp) ext=webp ;;
    *) echo "FAILED (not an image: $mime)"; rm -f "$tmp"; fail=1; continue ;;
  esac

  mv "$tmp" "$OUT/$key.$ext"
  chmod 644 "$OUT/$key.$ext"
  echo "ok  $OUT/$key.$ext  ($(du -h "$OUT/$key.$ext" | cut -f1))"
done <<< "$ASSETS"

if [ "$fail" -ne 0 ]; then
  echo
  echo "Some downloads failed; index.html left untouched." >&2
  exit 1
fi

# Rewrite each IMG map entry to its local file, matching on the key so the
# long signed URLs never have to be pasted anywhere.
python3 - "$PAGE" "$OUT" <<'PY'
import re, sys, pathlib

page, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
html = page.read_text(encoding="utf-8")

for f in sorted(out.iterdir()):
    key = f.stem
    pattern = re.compile(r'(\b%s\s*:\s*)"[^"]*"' % re.escape(key))
    html, n = pattern.subn(r'\1"%s/%s"' % (out.name, f.name), html)
    if n:
        print("  rewrote %s -> %s/%s" % (key, out.name, f.name))

page.write_text(html, encoding="utf-8")
PY

echo
echo "Done. Images are local; index.html no longer depends on signed URLs."
