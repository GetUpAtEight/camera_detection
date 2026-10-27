#!/usr/bin/env bash
# 验证远端媒体缓存：第一次提交 356MB 视频（需要上传），第二次应秒级返回
set -u
API=http://127.0.0.1:8080/api/detection/run
MEDIA=${MEDIA:-30dd89a0-8c2c-4759-ba6e-92a617161088}

payload() {
  cat <<JSON
{"mediaId":"$MEDIA",
 "algorithms":["laplacian","entropy","scene_diff","orb","edge"],
 "params":{},
 "sampleFps":2,
 "maxFrames":400,
 "refGapMs":1000,
 "segments":[
   {"label":"moved-15-20","type":"moved","startMs":15000,"endMs":20000},
   {"label":"covered-544-568","type":"covered","startMs":544000,"endMs":568000},
   {"label":"covered-660-675","type":"covered","startMs":660000,"endMs":675000}
 ]}
JSON
}

echo "== 第 $1 次提交 =="
start=$(date +%s%3N)
resp=$(curl -s -m 900 -X POST "$API" -H 'Content-Type: application/json' -d "$(payload)")
end=$(date +%s%3N)
echo "耗时 $((end-start)) ms"
echo "$resp" | head -c 500
echo
