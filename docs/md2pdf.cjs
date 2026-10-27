// Markdown（Mermaid の図を含む）を A4 の PDF にする
// 実行: NODE_PATH=$(npm root -g) node docs/md2pdf.cjs docs/設計書_hokenchat試作.md
// ponytail: marked と mermaid は CDN から読む（PDF を作るこの PC だけがネットにつながればよい。hokenchat 本体とは無関係）
const { chromium } = require("playwright");
const fs = require("fs"), path = require("path");

const src = path.resolve(process.argv[2]);
const out = src.replace(/\.md$/, ".pdf");
const md = fs.readFileSync(src, "utf8");

const html = `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<style>
  @page { size: A4; margin: 14mm 13mm 16mm; }
  body { font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif; color:#1d2330; font-size:9.5pt; line-height:1.7;
         -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  h1 { font-size:18pt; border-bottom:2px solid #1d2330; padding-bottom:6px; }
  h2 { font-size:13pt; margin-top:22px; padding:3px 0 3px 9px; border-left:5px solid #1f4fd1; page-break-after:avoid; }
  h3 { font-size:11pt; margin-top:14px; page-break-after:avoid; }
  h4 { font-size:10pt; margin:14px 0 2px; page-break-after:avoid; break-after:avoid; }
  table { border-collapse:collapse; width:100%; margin:6px 0 10px; font-size:8.6pt; page-break-inside:auto; }
  tr { page-break-inside:avoid; }
  th, td { border:1px solid #d9dee7; padding:4px 7px; text-align:left; vertical-align:top; }
  th { background:#f3f5f9; }
  code { font-family:Menlo,monospace; font-size:8.4pt; background:#f3f5f9; padding:0 3px; border-radius:3px; }
  pre code { display:block; padding:8px; }
  blockquote { margin:8px 0; padding:6px 12px; background:#f3f5f9; border-left:4px solid #9aa4b5; color:#4b5565; }
  hr { border:0; border-top:1px solid #d9dee7; margin:16px 0; }
  .mermaid { text-align:center; margin:10px 0; page-break-inside:avoid; }
  .mermaid svg { max-width:100% !important; height:auto; }  /* mermaid が付ける小さい max-width を上書き。幅は描画後に JS で決める */
</style></head><body><div id="c"></div>
<script>
  const md = ${JSON.stringify(md)};
  document.getElementById("c").innerHTML = marked.parse(md);
  document.querySelectorAll("pre code.language-mermaid").forEach(c => {
    const d = document.createElement("div"); d.className = "mermaid"; d.textContent = c.textContent; c.parentElement.replaceWith(d);
  });
  mermaid.initialize({ startOnLoad:false, theme:"neutral", fontFamily:"Hiragino Sans, sans-serif", gantt:{ useWidth:900 } });
  mermaid.run().then(() => {
    // 横長の図は紙の幅に合わせ、小さい図は拡大しすぎない（元の幅まで）
    document.querySelectorAll(".mermaid svg").forEach(s => {
      const w = s.viewBox.baseVal.width;
      s.style.setProperty("width", Math.min(s.parentElement.clientWidth, w) + "px", "important");
    });
    document.body.dataset.done = "1";
  }, e => { document.body.dataset.done = "err:" + e; });
</script></body></html>`;

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 900, height: 1200 } });
  await p.setContent(html, { waitUntil: "networkidle" });
  await p.waitForFunction(() => document.body.dataset.done, null, { timeout: 60000 });
  const done = await p.evaluate(() => document.body.dataset.done);
  if (done !== "1") throw new Error("Mermaid の描画に失敗: " + done);
  await p.pdf({ path: out, format: "A4", printBackground: true, preferCSSPageSize: true, displayHeaderFooter: true,
    headerTemplate: "<span></span>",
    footerTemplate: '<div style="width:100%;text-align:center;font-size:8px;color:#888"><span class=pageNumber></span> / <span class=totalPages></span></div>' });
  await b.close();
  console.log("出力:", out);
})();
