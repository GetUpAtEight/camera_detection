set -u
SVC=/root/yzn_model_api/ctd_service
PY=/root/yzn_model_api/.venv/bin/python
PORT=$(cat $SVC/port.txt | cut -d= -f2)
echo "port=$PORT"

echo "--- 0) synthesise bursts (distinct noise per sample time) ---"
$PY - <<'PYEOF'
import cv2, numpy as np
base = cv2.imread('/root/yzn_model_api/ctd_service/samples/reference_normal.jpg')
assert base is not None, 'reference missing'
covered = np.zeros_like(base); covered[:, :] = (38, 40, 42)
defocused = cv2.GaussianBlur(base, (0, 0), 6.0)
shifted = cv2.warpAffine(base, np.float32([[1, 0, 26], [0, 1, 12]]),
                         (base.shape[1], base.shape[0]), borderMode=cv2.BORDER_REPLICATE)
variants = {'n0': (base, 100), 'n1': (base, 200), 'c2': (covered, 300),
            'd3': (defocused, 400), 's5': (shifted, 500)}
for tag, (img, seed) in variants.items():
    for i in range(3):
        rng = np.random.default_rng(seed + i)
        noisy = np.clip(img.astype(np.float32) + rng.normal(0, 2.5, img.shape), 0, 255).astype(np.uint8)
        cv2.imwrite('/tmp/%s_%d.jpg' % (tag, i), noisy, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
print('  frames ready:', sorted(variants))
PYEOF

post() { ts=$1; shift; curl -s -m 90 -X POST "http://127.0.0.1:$PORT/sparse/sample" -F "cameraId=cam-http" -F "tsMs=$ts" "$@"; }
show() {
  $PY - "$1" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print('  !! not json:', open(sys.argv[1]).read()[:200]); raise SystemExit(0)
if 'detail' in d:
    print('  !! error:', d['detail']); raise SystemExit(0)
L0 = (d.get('layers') or {}).get('L0') or {}
L1 = (d.get('layers') or {}).get('L1') or {}
print('  verdict=%-24s alarm=%-5s type=%-9s risk=%s' % (
    d.get('verdict'), d.get('alarm'), d.get('tamperType'), d.get('risk')))
print('  matrix=%s' % json.dumps(d.get('matrix')))
print('  L0 dHist=%s entDrop=%s sharpDrop=%s edgeDrop=%s' % (
    L0.get('dHist'), L0.get('entropyDrop'), L0.get('sharpnessDrop'), L0.get('edgeDrop')))
print('  L1 quad=%s intraMad=%s temporalSigma=%s orb=%spx' % (
    L1.get('quadrant'), L1.get('intraMad'), L1.get('sigmaPerFrame'), L1.get('intraOrbTranslation')))
print('  baseline=%s' % json.dumps(d.get('baseline')))
print('  reason=%s' % ((d.get('reasons') or [''])[0][:110]))
PYEOF
}

B=1700000000000
curl -s -m 20 -X DELETE "http://127.0.0.1:$PORT/sparse/state/cam-http" >/dev/null

echo "[1] normal @t0"
post $B -F "frames=@/tmp/n0_0.jpg" -F "frames=@/tmp/n0_1.jpg" -F "frames=@/tmp/n0_2.jpg" > $SVC/r1.json; show $SVC/r1.json

echo "[2] normal @t0+1h (different frames, must stay normal)"
post $((B+3600000)) -F "frames=@/tmp/n1_0.jpg" -F "frames=@/tmp/n1_1.jpg" -F "frames=@/tmp/n1_2.jpg" > $SVC/r2.json; show $SVC/r2.json

echo "[3] covered @t0+2h"
post $((B+7200000)) -F "frames=@/tmp/c2_0.jpg" -F "frames=@/tmp/c2_1.jpg" -F "frames=@/tmp/c2_2.jpg" > $SVC/r3.json; show $SVC/r3.json

echo "[4] defocused @t0+3h"
post $((B+10800000)) -F "frames=@/tmp/d3_0.jpg" -F "frames=@/tmp/d3_1.jpg" -F "frames=@/tmp/d3_2.jpg" > $SVC/r4.json; show $SVC/r4.json

echo "[5] replay exact t0 frames @t0+4h"
post $((B+14400000)) -F "frames=@/tmp/n0_0.jpg" -F "frames=@/tmp/n0_1.jpg" -F "frames=@/tmp/n0_2.jpg" > $SVC/r5.json; show $SVC/r5.json

echo "[6] shifted @t0+5h"
post $((B+18000000)) -F "frames=@/tmp/s5_0.jpg" -F "frames=@/tmp/s5_1.jpg" -F "frames=@/tmp/s5_2.jpg" > $SVC/r6.json; show $SVC/r6.json

echo "--- 7) camera state (raw) ---"
curl -s -m 15 "http://127.0.0.1:$PORT/sparse/state/cam-http" | head -c 400; echo
echo "--- 8) cameras list ---"
curl -s -m 15 "http://127.0.0.1:$PORT/sparse/cameras" | head -c 400; echo
