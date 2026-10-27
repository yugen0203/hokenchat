#!/bin/zsh
# 録画した webm を mp4 に変換し、全件をつないだ動画と結果一覧を作る
# 実行: zsh tests/make_videos.sh   （先に tests/record_tests.cjs で録画しておく）
set -e
cd "$(dirname "$0")/../test-videos"
: > list.txt
for f in T*.webm; do
  ffmpeg -y -loglevel error -i "$f" -c:v libx264 -pix_fmt yuv420p -r 25 -crf 23 "${f%.webm}.mp4"
  echo "file '${f%.webm}.mp4'" >> list.txt
done
ffmpeg -y -loglevel error -f concat -safe 0 -i list.txt -c copy "00_全テスト通し.mp4"
rm list.txt T*.webm
python3 -I - <<'EOF'
import json
rs = json.load(open("results.json"))
ok = sum(r["ok"] for r in rs)
lines = [f"# hokenchat 仕様テスト結果（{ok}/{len(rs)} 合格）", "",
         "| No | テスト内容 | 期待する結果 | 判定 | 動画 |", "|---|---|---|---|---|"]
for r in rs:
    mp4 = r["file"].replace(".webm", ".mp4")
    lines.append(f"| {r['id']} | {r['title']} | {r['expect']} | {'合格' if r['ok'] else '**不合格**'} | [{mp4}]({mp4}) |")
lines += ["", "全件をつないだ動画：[00_全テスト通し.mp4](00_全テスト通し.mp4)",
          "", "※T07〜T12 は「疑似お客さま」（LINEを通さず、受付サーバーと同じ本体APIを呼ぶテスト用の利用者）で確認。T13 は本物のLINEに送信。"]
open("結果一覧.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
EOF
