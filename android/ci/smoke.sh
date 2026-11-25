#!/usr/bin/env bash
# Runs inside the Android emulator job: installs the APK, starts the app and drives its private server
# (forwarded to the host) with real uploads, so Python, OCR and PDF rendering are proven ON ANDROID.
set -euo pipefail
APK="$1"; SAMPLES="$2"
PKG=app.stmtcheck.mobile

adb install -r "$APK"
adb logcat -c
adb shell am start -n "$PKG/app.stmtcheck.MainActivity"

dump_app_log() {
  echo "=== app process: $(adb shell pidof "$PKG" || echo 'NOT RUNNING')"
  adb logcat -d -v time | grep -E "StmtCheck|AndroidRuntime|FATAL EXCEPTION|[Cc]haquopy|[Pp]ython|app\.stmtcheck|System\.err|ANR in|UnsatisfiedLink|ClassNotFound|NoClassDefFound" | tail -150 || true
}
for i in $(seq 1 40); do
  LINE=$(adb logcat -d -s StmtCheck:I StmtCheck:E | grep -E "STMTCHECK_READY|startup failed" | tail -1 || true)
  [ -n "$LINE" ] && break
  sleep 3
done
echo "app said: ${LINE:-nothing}" | sed 's/token=.*/token=<hidden>/'
case "$LINE" in *STMTCHECK_READY*) ;; *) echo "App did not start"; dump_app_log; exit 1;; esac
PORT=$(echo "$LINE" | sed -E 's/.*port=([0-9]+).*/\1/')
TOKEN=$(echo "$LINE" | sed -E 's/.*token=([0-9a-f]+).*/\1/')
adb forward "tcp:$PORT" "tcp:$PORT"
BASE="http://127.0.0.1:$PORT"
JAR=$(mktemp)

echo "--- no session must be refused"
test "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" = 302
echo "--- wrong bootstrap token must 404"
test "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/_bootstrap/deadbeef")" = 404
echo "--- sign in with the app's secret"
curl -s -c "$JAR" -L "$BASE/_bootstrap/$TOKEN" | grep -q "Check a statement"
HOME_HTML=$(curl -s -b "$JAR" "$BASE/")
CSRF=$(echo "$HOME_HTML" | grep -o 'name="csrf" value="[0-9a-f]*"' | head -1 | sed -E 's/.*value="([0-9a-f]+)".*/\1/')
test -n "$CSRF"

trap 'echo "--- script failed; app log:"; dump_app_log' ERR
check() {  # file expected-text label
  OUT=$(curl -s -b "$JAR" -F "csrf=$CSRF" -F "against=none" -F "file=@$1" "$BASE/analyze")
  if echo "$OUT" | grep -q "$2"; then echo "PASS: $3"; else echo "FAIL: $3"; echo "$OUT" | sed 's/<[^>]*>/ /g' | tr -s ' \n' | head -c 1500; exit 1; fi
}
check "$SAMPLES/tampered.pdf" "STRONG INDICATORS" "text-layer PDF on device"
check "$SAMPLES/clean.pdf" "NO INDICATORS" "clean PDF on device (no false alarm)"
check "$SAMPLES/scan_tampered.jpg" "read by OCR" "scanned photo through ML Kit OCR"
check "$SAMPLES/scan_tampered.jpg" "running-balance" "OCR result reaches the arithmetic checks"
echo "--- scanned PDF through PdfRenderer + OCR"
python3 - "$SAMPLES" <<'PY'
import sys
from PIL import Image
Image.open(f"{sys.argv[1]}/scan_tampered.jpg").convert("RGB").save(f"{sys.argv[1]}/scan_tampered_flat.pdf", "PDF", resolution=200)
PY
check "$SAMPLES/scan_tampered_flat.pdf" "no-text-layer" "flattened PDF detected and rendered on device"
check "$SAMPLES/scan_tampered_flat.pdf" "running-balance" "flattened PDF read by OCR"

echo "--- library on device + export download"
curl -s -b "$JAR" -F "csrf=$CSRF" -F "label=CI original" -F "file=@$SAMPLES/clean.pdf" "$BASE/library/add" | grep -q "Stored"
RES=$(curl -s -b "$JAR" -F "csrf=$CSRF" -F "against=auto" -F "file=@$SAMPLES/tampered.pdf" "$BASE/analyze")
EXP=$(echo "$RES" | grep -o '/export/[A-Za-z0-9_-]*' | head -1)
test -n "$EXP"
curl -s -b "$JAR" "$BASE$EXP?fmt=pdf" | head -c 4 | grep -q "%PDF"
echo "PASS: library comparison and PDF export"
echo "ALL ON-DEVICE CHECKS PASSED"
