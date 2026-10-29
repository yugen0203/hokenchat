// hokenchat 画面（第0.2版）。外部のライブラリ・CDN は使わない
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
let ME = null, META = null, CUR = "internal";

async function api(path, body) {
  const init = body !== undefined ? {method: "POST", headers: {"Content-Type": "application/json", "X-HC": "1"}, body: JSON.stringify(body)} : {};
  let r, j;
  try { r = await fetch(path, init); } catch (e) { return {error: "サーバーに接続できません：" + e}; }
  try { j = await r.json(); } catch { j = {error: `通信エラー（${r.status}）`}; }
  if (r.status === 401 && ME) { location.reload(); }
  if (r.status === 428) needPurpose();
  if (j && j.ai_stopped) health();
  return j;
}
async function upload(path, file, headers) {
  const h = {"X-HC": "1"};
  for (const [k, v] of Object.entries(headers)) h[k] = encodeURIComponent(v ?? "");
  try { return await (await fetch(path, {method: "POST", body: file, headers: h})).json(); }
  catch (e) { return {error: String(e)}; }
}
const can = p => ME && ME.perms.includes(p);
const allowed = () => (ME?.purpose && META.purpose_fields[ME.purpose]) || null;
const say = (el, r, okText) => { el.className = "msgline " + (r.error ? "ng" : "ok"); el.textContent = r.error || okText || "保存しました"; };
const today = () => new Date().toISOString().slice(0, 10);
const openFile = url => window.open(url, "_blank", "width=900,height=900");

// ---------- モーダル ----------
function modal(html) { $("#mbox").innerHTML = html; $("#modal").classList.add("on"); return $("#mbox"); }
function closeModal() { $("#modal").classList.remove("on"); }
$("#modal").addEventListener("mousedown", e => { if (e.target.id === "modal" && !$("#modal").dataset.lock) closeModal(); });
// CSP でインラインの onclick は動かないので、閉じる・印刷のボタンはここでまとめて受ける
document.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeModal(); else if (e.target.closest("[data-print]")) window.print(); });

// =================== ログイン・初期設定 ===================
async function boot() {
  const r = await api("/api/me");
  if (r.setup_needed) return gateSetup();
  if (!r.staff) return gateLogin();
  ME = r.staff; META = r.meta;
  if (ME.must_change) return gatePassword(true);
  startApp();
}
function gateBox(inner) {
  $("#gate").innerHTML = `<div class="brand"><i>保</i>hokenchat</div><div class="brand-sub">社内AIアシスタント（試作）・この PC の中だけで動きます</div>${inner}`;
  document.body.classList.add("locked");
  $("#gate").querySelector("input")?.focus();
}
function gateSetup() {
  gateBox(`<p class="hint">はじめに<b>管理者（生成AI責任者）</b>のアカウントを1つ作ります。社員のアカウントは、ログイン後に「社員・設定」で追加します。</p>
    <form id="gf"><label class="f"><span>ログインID</span><input class="i" name="login" autocomplete="username"></label>
    <label class="f"><span>氏名</span><input class="i" name="name"></label>
    <label class="f"><span>パスワード（10文字以上・英字と数字）</span><input class="i" type="password" name="password" autocomplete="new-password"></label>
    <label class="f"><span>パスワード（確認）</span><input class="i" type="password" name="password2" autocomplete="new-password"></label>
    <button class="btn p">管理者を作成</button><div class="msgline" id="gm"></div></form>`);
  $("#gf").onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (f.password !== f.password2) return say($("#gm"), {error: "パスワードが一致しません"});
    const r = await api("/api/setup", f);
    if (r.error) return say($("#gm"), r);
    gateLogin("管理者を作成しました。ログインすると、認証アプリ（多要素認証）の登録に進みます。");
  };
}
function gateLogin(msg) {
  gateBox(`<form id="gf"><label class="f"><span>ログインID</span><input class="i" name="login" autocomplete="username"></label>
    <label class="f"><span>パスワード</span><input class="i" type="password" name="password" autocomplete="current-password"></label>
    <button class="btn p">次へ</button><div class="msgline ${msg ? "ok" : ""}" id="gm">${esc(msg || "")}</div></form>`);
  $("#gf").onsubmit = async e => {
    e.preventDefault();
    const r = await api("/api/login", Object.fromEntries(new FormData(e.target)));
    if (r.error) return say($("#gm"), r);
    gateCode(r);
  };
}
function gateCode(t) {
  const enroll = t.enroll ? `<p class="hint"><b>${esc(t.name)}</b> さん、はじめに<b>認証アプリ</b>を登録します（多要素認証）。<br>
      スマホの認証アプリ（Google Authenticator・Microsoft Authenticator など）で「セットアップキーを入力」を選び、次のキーを入れてください（種類：時間ベース）。</p>
      <div class="secret mono">${esc(t.secret.match(/.{1,4}/g).join(" "))}</div>
      <details><summary>QRコードを読めるアプリ向けのURL</summary><div class="mono" style="margin-top:6px">${esc(t.uri)}</div></details>
      <p class="hint" style="margin-top:10px">登録できたら、アプリに表示された6桁を入れてください。</p>`
    : `<p class="hint">${esc(t.name)} さん、認証アプリに表示されている6桁の確認コードを入れてください。</p>`;
  gateBox(`${enroll}<form id="gf"><input class="i code-in" name="code" inputmode="numeric" maxlength="7" autocomplete="one-time-code" placeholder="000000">
    <button class="btn p">${t.enroll ? "登録してログイン" : "ログイン"}</button><div class="msgline" id="gm"></div>
    <button type="button" class="btn s" id="gback">最初からやり直す</button></form>`);
  $("#gback").onclick = () => gateLogin();
  $("#gf").onsubmit = async e => {
    e.preventDefault();
    const r = await api("/api/login/code", {ticket: t.ticket, code: e.target.code.value});
    if (r.error) return say($("#gm"), r);
    boot();
  };
}
function pwForm(forced) {
  return `<p class="hint">${forced ? "最初のログインです。管理者から受け取った仮のパスワードを、自分だけが知るパスワードに変えてください。" : "パスワードを変更します。"}</p>
    <form id="pwf"><label class="f"><span>今のパスワード</span><input class="i" type="password" name="old" autocomplete="current-password"></label>
    <label class="f"><span>新しいパスワード（10文字以上・英字と数字）</span><input class="i" type="password" name="new" autocomplete="new-password"></label>
    <label class="f"><span>新しいパスワード（確認）</span><input class="i" type="password" name="new2" autocomplete="new-password"></label>
    <div class="row" style="margin-top:6px"><button class="btn p">変更する</button>${forced ? "" : `<button type="button" class="btn" data-close>閉じる</button>`}</div>
    <div class="msgline" id="pwm"></div></form>`;
}
function bindPw(done) {
  $("#pwf").onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (f.new !== f.new2) return say($("#pwm"), {error: "新しいパスワードが一致しません"});
    const r = await api("/api/password", f);
    if (r.error) return say($("#pwm"), r);
    done();
  };
}
function gatePassword(forced) { gateBox(pwForm(forced)); bindPw(() => boot()); }
$("#pwbtn").onclick = () => { modal(`<h2>パスワードの変更</h2>${pwForm(false)}`); bindPw(() => { say($("#pwm"), {}, "変更しました"); setTimeout(closeModal, 800); }); };
$("#logout").onclick = async () => { await api("/api/logout", {}); location.reload(); };

// =================== 画面の切り替え ===================
const VIEWS = {
  internal: ["社内チャット", "約款・規程を検索し、根拠の原文を出典つきで表示します（AIは判断をしません）"],
  line: ["公式LINE", "1:1トーク。AIモードは承認済みの定型文で自動案内、手動モードは担当者が返信します"],
  customers: ["顧客DB", "利用目的に必要な項目だけを表示します。要配慮情報・マイナンバー・カード番号の項目はありません"],
  mailing: ["郵送", "送付状・封筒・宛名ラベルを顧客DBから作成。作成者とは別の担当者が照合してから発送します"],
  quotes: ["見積依頼書", "依頼文から下書き → 人が確認・承認 → J-NET入力票 → J-NETへは人が手入力 → 結果と突合"],
  tasks: ["タスク", "依頼を「誰が・何を・いつまでに」に整理します"],
  docs: ["登録文書", "RAG（検索）の対象。登録した版は管理者の承認後に検索対象になり、古い版は自動で外れます"],
  vault: ["文書保管庫", "申込書控えを暗号化して保管。種目別の保存期間（自動車3年・火災5年・その他3年）が過ぎたら自動で消去します"],
  audit: ["監査ログ", "誰が・いつ・何の目的で・何をしたか（本文は保存しない）。改ざんはハッシュチェーンで検出します"],
  monthly: ["月次点検", "禁止情報の入力・ログの改ざん・削除件数の照合・権限の棚卸しを毎月確認します"],
  admin: ["社員・設定", "アカウント・ロール・AI停止スイッチ・差出人情報"],
};
const LOAD = {};
function show(v) {
  CUR = v;
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".view").forEach(s => s.classList.toggle("on", s.id === "v-" + v));
  [$("#title").textContent, $("#sub").textContent] = VIEWS[v];
  LOAD[v]?.();
}
function startApp() {
  document.body.classList.remove("locked");
  $("#me").innerHTML = `<b>${esc(ME.name)}</b>（${esc(ME.role_name)}）`;
  document.querySelectorAll("#nav [data-p]").forEach(el => el.hidden = !can(el.dataset.p));
  document.querySelectorAll("#nav button").forEach(b => b.onclick = () => show(b.dataset.v));
  const sel = $("#purpose");
  sel.innerHTML = `<option value="">（選んでください）</option><optgroup label="顧客情報を使う">${META.purposes.map(p => `<option>${esc(p)}</option>`).join("")}</optgroup>
    <optgroup label="顧客情報を使わない">${META.purposes_other.map(p => `<option>${esc(p)}</option>`).join("")}</optgroup>`;
  sel.value = ME.purpose || "";
  sel.onchange = async () => {
    const r = await api("/api/purpose", {purpose: sel.value});
    if (r.error) return alert(r.error);
    ME.purpose = r.purpose; sel.classList.remove("need"); LOAD[CUR]?.();
  };
  initChat(); initLine();
  show("internal");
  health(); setInterval(health, 15000);
  notifs(); setInterval(notifs, 30000);
  counts(); setInterval(counts, 60000);
}
function needPurpose() { $("#purpose").classList.add("need"); $("#purpose").focus(); }
function purposeGate(el, fields, what) {
  const a = allowed();
  if (a && fields.every(f => a.includes(f))) return a;
  el.innerHTML = `<div class="card"><h2><i class="fa-solid fa-tag"></i>利用目的を選んでください</h2>
    <p class="hint">${esc(what)}には顧客情報を使います。画面右上の「利用目的」で目的を選ぶと、その目的に必要な項目だけが表示されます（要件 F-02）。<br>
    使える目的：${esc(META.purposes.filter(p => fields.every(f => META.purpose_fields[p].includes(f))).join("・"))}</p></div>`;
  needPurpose();
  return null;
}

// ---------- 死活・通知・件数 ----------
async function health() {
  const r = await fetch("/api/health").then(x => x.json()).catch(() => ({}));
  const st = $("#status"), dot = st.querySelector(".dot");
  dot.className = "dot" + (!r.ai_enabled ? " stop" : r.llm ? "" : " ng");
  st.children[1].textContent = !r.ai_enabled ? "AI 停止中（管理者）" : r.llm ? "ローカルLLM 接続中" : "ローカルLLM 停止中";
  $("#model").textContent = r.model || "-";
  $("#stopbar").classList.toggle("on", r.ai_enabled === false);
}
let NOTIFS = [];
async function notifs() {
  NOTIFS = await api("/api/notifications");
  if (!Array.isArray(NOTIFS)) NOTIFS = [];
  const n = NOTIFS.filter(x => !x.read).length;
  $("#bellcnt").hidden = !n; $("#bellcnt").textContent = n > 99 ? "99+" : n;
}
$("#bell").onclick = async () => {
  const d = $("#drawer");
  if (d.classList.toggle("on")) {
    d.innerHTML = NOTIFS.length ? NOTIFS.map(x => `<div class="n ${x.read ? "" : "unread"} ${esc(x.level)}"><b>${esc(x.title)}</b><div class="pre">${esc(x.body)}</div><small class="time">${esc(x.ts)}</small></div>`).join("")
      : `<div class="empty">通知はありません</div>`;
    const ids = NOTIFS.filter(x => !x.read).map(x => x.id);
    if (ids.length) { await api("/api/notifications/read", {ids}); notifs(); }
  }
};
document.addEventListener("mousedown", e => { if (!e.target.closest("#drawer,#bell")) $("#drawer").classList.remove("on"); });
async function counts() {
  const set = (id, n) => { $(id).hidden = !n; $(id).textContent = n; };
  if (can("docs.approve")) { const d = await api("/api/docs"); if (Array.isArray(d)) set("#cnt-docs", d.filter(x => x.status === "pending").length); }
  if (can("vault.approve")) { const q = await api("/api/vault/requests"); if (Array.isArray(q)) set("#cnt-vault", q.length); }
  if (can("task")) { const t = await api("/api/tasks"); if (t.summary) set("#cnt-tasks", t.summary.overdue); }
}

// =================== 社内チャット ===================
const SAMPLES = ["車両保険でタイヤだけパンクした場合は支払われますか？", "地震で家が燃えたら火災保険は出ますか？", "申込書控えは何年保存する？",
  "保険金の請求に必要な書類は？", "社用車のガソリン代の精算方法は？"];
function renderAnswer(r) {
  if (r.error) return `<span class="warn">${esc(r.error)}</span>`;
  if (r.blocked) return `<span class="warn">${esc(r.answer)}</span>`;
  let h = "";
  if (r.quotes?.length) {
    h = "該当しうる条文：" + r.quotes.map(q => `<div class="quote"><b>${esc(q.doc)}（${esc(q.version)}）${esc(q.article)}</b>
      ${q.before ? `<div class="ctx">${esc(q.before)}</div>` : ""}<div><span class="hit">「${esc(q.line)}」</span></div>${q.after ? `<div class="ctx">${esc(q.after)}</div>` : ""}</div>`).join("")
      + `<div class="time">黄色がAIの選んだ行、灰色は前後の行です（AIが隣の行を選ぶことがあるため、前後もあわせて確認してください）</div>`;
  } else h = esc(r.answer);
  if (r.notice) h += `<div class="notice">${esc(r.notice)}</div>`;
  if (r.sources?.length) h += `<details><summary>検索した候補の条文（${r.sources.length}件）</summary>` + r.sources.map(s =>
    `<div class="src"><b>${esc(s.doc)}（${esc(s.version)}）${esc(s.article)}</b>　類似度 ${s.score}<br>${esc(s.text)}</div>`).join("") + "</details>";
  return h;
}
function initChat() {
  const log = $("#ilog"), form = $("#iform"), input = form.querySelector("input"), btn = form.querySelector("button");
  const add = (cls, html) => { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; log.append(d); log.scrollTop = log.scrollHeight; return d; };
  const send = async text => {
    if (!text.trim() || btn.disabled) return;
    add("me", esc(text)); input.value = ""; btn.disabled = true;
    const wait = add("ai", "…"), t0 = performance.now();
    const s = await api("/api/search", {text});
    if (s.sources?.length) wait.innerHTML = `<span class="time">AIが該当する行を絞り込み中…（先に検索した候補を表示）</span>` + renderAnswer({sources: s.sources}).replace("<details>", "<details open>");
    const r = await api("/api/internal", {text});
    wait.innerHTML = renderAnswer(r) + `<div class="time">${((performance.now() - t0) / 1000).toFixed(1)}秒</div>`;
    btn.disabled = false; input.focus(); log.scrollTop = log.scrollHeight;
  };
  form.onsubmit = e => { e.preventDefault(); send(input.value); };
  $("#isamples").innerHTML = "";
  for (const s of SAMPLES) { const b = document.createElement("button"); b.type = "button"; b.textContent = s; b.onclick = () => send(s); $("#isamples").append(b); }
}

// =================== 公式LINE ===================
let LCUR = null, lastId = -1;
const hm = ts => (ts || "").slice(11, 16);
const LNOTE = {ai: "AIモード：お客さまのメッセージをAIが分類し、承認済みの定型文で自動返信します。担当者が割り込んで送ることもできます。",
               manual: "手動モード：AIは返信しません。担当者が返信します。「AIの下書き」で定型文の候補を出して直せます。"};
function initLine() {
  LOAD.line = () => { loadUsers(); loadMsgs(true); };
  setInterval(() => { if (CUR === "line") { loadUsers(); loadMsgs(); } }, 3000);
  $("#lmode").querySelectorAll("button").forEach(b => b.onclick = async () => {
    if (!LCUR || LCUR.mode === b.dataset.m) return;
    await api("/api/line/mode", {user_id: LCUR.user_id, mode: b.dataset.m});
    LCUR.mode = b.dataset.m; setLMode(); loadUsers(); loadMsgs(true);
  });
  const ta = $("#lform textarea");
  $("#lform").onsubmit = async e => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text || !LCUR) return;
    const r = await api("/api/line/send", {user_id: LCUR.user_id, text, from_draft: ta.dataset.draft === "1"});
    if (r.error) return alert("送信できませんでした：" + r.error);
    ta.value = ""; ta.dataset.draft = ""; loadMsgs(true); loadUsers();
  };
  $("#ldraft").onclick = async () => {
    const r = await api("/api/line/draft", {user_id: LCUR.user_id});
    if (r.error) return alert(r.error);
    ta.value = r.draft; ta.dataset.draft = "1"; ta.focus();
    $("#lhint").textContent = `下書き（分類：${r.intent || "ガード"}${r.handoff ? "・担当者対応が必要" : ""}）。${r.note}`;
  };
  $("#llink").onclick = linkCustomer;
}
async function loadUsers() {
  const us = await api("/api/line/users");
  if (!Array.isArray(us)) return;
  $("#lusers").innerHTML = us.length ? us.map(u => `<div class="item ${u.user_id === LCUR?.user_id ? "on" : ""}" data-id="${esc(u.user_id)}">
      <div class="av">${esc((u.name || "?")[0])}</div><div class="t"><b>${esc(u.name)}<span class="md ${u.mode === "manual" ? "m" : ""}">${u.mode === "manual" ? "手動" : "AI"}</span>${u.customer_id ? `<span class="md c">顧客${u.customer_id}</span>` : ""}</b>
      <small>${esc(u.last || "")}</small></div><small>${hm(u.ts)}</small></div>`).join("")
    : `<div class="empty">まだトークがありません。<br>スマホのLINEで検証用アカウントを友だち追加して、メッセージを送ってください。</div>`;
  $("#lusers").querySelectorAll(".item").forEach(el => el.onclick = () => openTalk(us.find(u => u.user_id === el.dataset.id)));
  if (LCUR) { const u = us.find(x => x.user_id === LCUR.user_id); if (u) LCUR = u; setLMode(); }
}
function setLMode() {
  $("#ltools").hidden = false;
  $("#lmode").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.m === LCUR.mode));
  if (!$("#lform textarea").dataset.draft) $("#lhint").textContent = LNOTE[LCUR.mode];
  $("#llink span").textContent = LCUR.customer_id ? `顧客ID ${LCUR.customer_id}` : "顧客とひも付け";
}
async function loadMsgs(force) {
  if (!LCUR) return;
  const ms = await api("/api/line/msgs?user=" + encodeURIComponent(LCUR.user_id));
  if (!Array.isArray(ms)) return;
  const top = ms.length ? ms[ms.length - 1].id : 0;
  if (!force && top === lastId) return;
  lastId = top;
  const att = m => m.attach_id ? `<div class="att"><button class="btn s" data-att="${m.attach_id}"><i class="fa-regular fa-file"></i>担当者が開く</button></div>` : "";
  $("#lmsgs").innerHTML = ms.map(m => m.sender === "system" ? `<div class="lsys">${esc(m.text)} ${hm(m.ts)}</div>`
    : m.sender === "customer"
      ? `<div class="lrow in"><div class="av g">${esc((LCUR.name || "?")[0])}</div><div><div class="nm">${esc(LCUR.name)}</div><div style="display:flex;align-items:flex-end;gap:6px"><div class="bubble">${esc(m.text)}${att(m)}${m.note ? `<div class="dbg h">${esc(m.note)}</div>` : ""}</div><div class="lmeta">${hm(m.ts)}</div></div></div></div>`
      : `<div class="lrow out"><div><div class="who">${m.sender === "ai" ? "AI自動返信" : "担当者"}</div><div style="display:flex;flex-direction:row-reverse;align-items:flex-end;gap:6px"><div class="bubble">${esc(m.text)}${m.note ? `<div class="dbg">${esc(m.note)}</div>` : ""}</div><div class="lmeta">${hm(m.ts)}</div></div></div></div>`).join("");
  $("#lmsgs").querySelectorAll("[data-att]").forEach(b => b.onclick = () => openFile("/api/line/attach?id=" + b.dataset.att));
  $("#lmsgs").scrollTop = $("#lmsgs").scrollHeight;
}
function openTalk(u) {
  LCUR = u; lastId = -1; $("#lname").textContent = u.name;
  $("#lform").querySelectorAll("textarea,button").forEach(e => e.disabled = false);
  $("#lform textarea").dataset.draft = "";
  setLMode(); loadUsers(); loadMsgs(true);
}
async function linkCustomer() {
  if (!ME.purpose || ME.purpose !== "お客さま対応") { alert("顧客とのひも付けは、利用目的「お客さま対応」を選んでから行ってください（本人確認のあとに行います）"); return needPurpose(); }
  const box = modal(`<h2>顧客DBとひも付け</h2><p class="hint">お電話などで<b>本人確認が済んだ場合だけ</b>ひも付けてください。LINEの表示名だけで判断しないでください。</p>
    <div class="row"><input class="i" id="lq" placeholder="氏名・フリガナ・電話で検索" style="flex:1"><button class="btn" id="lqb">検索</button></div>
    <div id="lqr" style="margin-top:10px"></div><div class="row end" style="margin-top:12px">${LCUR.customer_id ? `<button class="btn d" id="lunlink">ひも付けを外す</button>` : ""}<button class="btn" data-close>閉じる</button></div>`);
  const find = async () => {
    const rs = await api("/api/customers?q=" + encodeURIComponent($("#lq").value));
    $("#lqr").innerHTML = Array.isArray(rs) && rs.length ? `<div class="tbl"><table>${rs.map(c => `<tr class="click" data-id="${c.id}"><td>${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.kana || "")}</td><td>${esc(c.phone || "")}</td></tr>`).join("")}</table></div>` : `<div class="empty">${esc(rs.error || "見つかりません")}</div>`;
    $("#lqr").querySelectorAll("tr").forEach(tr => tr.onclick = async () => {
      if (!confirm("本人確認は済んでいますか？ この顧客とひも付けます")) return;
      const r = await api("/api/line/link", {user_id: LCUR.user_id, customer_id: +tr.dataset.id});
      if (r.error) return alert(r.error);
      closeModal(); loadUsers(); loadMsgs(true);
    });
  };
  box.querySelector("#lqb").onclick = find; find();
  box.querySelector("#lunlink")?.addEventListener("click", async () => { await api("/api/line/link", {user_id: LCUR.user_id, customer_id: null}); closeModal(); loadUsers(); loadMsgs(true); });
}

// =================== 顧客DB ===================
const CF = ["name", "kana", "postal", "address", "phone", "email", "note"];
const PF = ["insurer", "kind", "policy_no", "start", "expiry"];
let CSEL = null;
LOAD.customers = () => {
  const el = $("#p-customers"), a = purposeGate(el, ["name"], "顧客DB");
  if (!a) return;
  const edit = ME.purpose === "契約管理";
  el.innerHTML = `<div class="cols"><div>
      <div class="card"><h2><i class="fa-solid fa-magnifying-glass"></i>検索</h2><div class="row"><input class="i" id="cq" placeholder="氏名・フリガナ・電話・顧客ID" style="flex:1"><button class="btn" id="cqb">検索</button>
        ${edit ? `<button class="btn p" id="cnew"><i class="fa-solid fa-plus"></i>新規</button>` : ""}</div>
        <div id="clist" style="margin-top:10px"></div></div>
      ${edit ? `<div class="card"><h2><i class="fa-solid fa-wand-magic-sparkles"></i>入力支援 <small>メモ・名刺の文字から項目を下書き</small></h2>
        <p class="hint">メモをAIが項目に分けて右の入力欄に入れます（下書き）。<b>登録はしません</b>。原本と照らして確認してから登録してください。<br>病歴・けがの内容・マイナンバー・カード番号を含むメモはAIに送らずに止めます。</p>
        <textarea class="i" id="cmemo" placeholder="例：新規のお客さま 架空花子（カクウハナコ）様 〒530-0001 大阪市北区梅田1-2-3 サンプルビル5階 電話06-0000-0000 自動車保険 満期2027年4月1日"></textarea>
        <div class="row" style="margin-top:8px"><button class="btn" id="cext">項目に分ける</button></div><div class="msgline" id="cextm"></div></div>` : ""}
    </div><div><div class="card" id="cform"><div class="empty">左の一覧から顧客を選ぶ${edit ? "か、［新規］を押してください" : "と、利用目的「" + esc(ME.purpose) + "」で見られる項目だけを表示します"}</div></div></div></div>`;
  const find = async () => {
    const rs = await api("/api/customers?q=" + encodeURIComponent($("#cq").value));
    $("#clist").innerHTML = Array.isArray(rs) && rs.length ? `<div class="tbl"><table><tr><th>ID</th><th>氏名</th>${a.includes("phone") ? "<th>電話</th>" : ""}${a.includes("address") ? "<th>住所</th>" : ""}</tr>
      ${rs.map(c => `<tr class="click ${CSEL === c.id ? "sel" : ""}" data-id="${c.id}"><td>${c.id}</td><td>${esc(c.name)}<br><small class="time">${esc(c.kana || "")}</small></td>${a.includes("phone") ? `<td class="nw">${esc(c.phone)}</td>` : ""}${a.includes("address") ? `<td>${esc(c.address)}</td>` : ""}</tr>`).join("")}</table></div>`
      : `<div class="empty">${esc(rs.error || "該当する顧客がいません")}</div>`;
    $("#clist").querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = async () => { CSEL = +tr.dataset.id; find(); custForm(await api("/api/customer?id=" + CSEL), a, edit); });
  };
  $("#cqb").onclick = find; $("#cq").onkeydown = e => { if (e.key === "Enter") find(); };
  $("#cnew")?.addEventListener("click", () => { CSEL = null; custForm({policies: []}, a, edit); });
  $("#cext")?.addEventListener("click", async () => {
    $("#cextm").className = "msgline"; $("#cextm").textContent = "AIが項目に分けています…";
    const r = await api("/api/customers/extract", {text: $("#cmemo").value});
    if (r.error) return say($("#cextm"), r);
    const cur = CSEL ? {} : {policies: []};
    custForm({...cur, ...r.customer, id: CSEL || undefined, policies: r.policies}, a, edit, true);
    say($("#cextm"), {}, r.note);
  });
  find();
};
function custForm(c, a, edit, drafted) {
  const L = META.labels, ro = edit ? "" : "readonly";
  const fld = k => a.includes(k) ? `<label class="f ${k === "name" ? "req" : ""}"><span>${L[k]}</span>${k === "note" ? `<textarea class="i" name="${k}" ${ro}>${esc(c[k] || "")}</textarea>` : `<input class="i" name="${k}" value="${esc(c[k] || "")}" ${ro}>`}</label>` : "";
  const prow = p => `<tr>${PF.map(k => `<td><input class="i" data-p="${k}" value="${esc(p[k] || "")}" ${ro} ${k === "start" || k === "expiry" ? 'placeholder="YYYY-MM-DD"' : ""}></td>`).join("")}${edit ? `<td><button class="btn s d" data-del>×</button></td>` : ""}</tr>`;
  $("#cform").innerHTML = `<h2><i class="fa-regular fa-user"></i>${c.id ? "顧客ID " + c.id : "新規の顧客"}${drafted ? ` <span class="tag am">AIの下書き・未登録</span>` : ""}</h2>
    <form id="cf"><div class="grid2">${fld("name")}${fld("kana")}${fld("postal")}${fld("phone")}</div>${fld("address")}${fld("email")}${fld("note")}
    ${a.includes("policies") ? `<h2 style="margin-top:14px"><i class="fa-regular fa-file-lines"></i>契約概要</h2><div class="tbl"><table id="ptab"><tr>${PF.map(k => `<th>${L[k]}</th>`).join("")}${edit ? "<th></th>" : ""}</tr>${(c.policies || []).map(prow).join("")}</table></div>
      ${edit ? `<button type="button" class="btn s" id="padd" style="margin-top:6px"><i class="fa-solid fa-plus"></i>契約を追加</button>` : ""}` : ""}
    ${edit ? `<div class="row" style="margin-top:14px"><button class="btn p">${c.id ? "更新" : "登録"}</button>${c.id && can("staff.manage") ? `<span class="sp"></span><button type="button" class="btn d" id="cdel">削除</button>` : ""}</div>` : ""}
    <div class="msgline" id="cm"></div></form>`;
  if (!edit) return;
  const bindDel = () => $("#ptab")?.querySelectorAll("[data-del]").forEach(b => b.onclick = () => b.closest("tr").remove());
  bindDel();
  $("#padd")?.addEventListener("click", () => { $("#ptab").insertAdjacentHTML("beforeend", prow({})); bindDel(); });
  $("#cf").onsubmit = async e => {
    e.preventDefault();
    const b = Object.fromEntries(new FormData(e.target));
    b.id = c.id || null;
    b.policies = [...($("#ptab")?.querySelectorAll("tr") || [])].slice(1).map(tr => Object.fromEntries([...tr.querySelectorAll("[data-p]")].map(i => [i.dataset.p, i.value])));
    const r = await api("/api/customers/save", b);
    say($("#cm"), r, "登録しました");
    if (r.ok) { CSEL = r.id; LOAD.customers(); }
  };
  $("#cdel")?.addEventListener("click", async () => {
    if (!confirm("この顧客と契約概要を削除しますか？")) return;
    await api("/api/customers/delete", {id: c.id}); CSEL = null; LOAD.customers();
  });
}

// =================== 郵送 ===================
let MSEL = new Set();
LOAD.mailing = async () => {
  const el = $("#p-mailing"), a = purposeGate(el, ["name", "address"], "送付状・封筒の作成");
  if (!a) return;
  const kinds = Object.keys(META.doc_kinds);
  el.innerHTML = `<div class="cols"><div><div class="card"><h2><i class="fa-solid fa-pen"></i>送付物を作る</h2>
      <label class="f"><span>宛先（顧客DBから選ぶ。手入力で転記しない）</span><div class="row"><input class="i" id="mq" placeholder="氏名・フリガナで検索" style="flex:1"><button class="btn" id="mqb">検索</button></div></label>
      <div id="mres" style="margin:8px 0"></div><div class="hint" id="mpicked">選んだ宛先：なし</div>
      <div class="grid2"><label class="f"><span>送付の種類</span><select class="i" id="mkind">${kinds.map(k => `<option>${esc(k)}</option>`).join("")}</select></label>
      <label class="f"><span>封筒</span><select class="i" id="menv">${META.envelopes.map(k => `<option>${esc(k)}</option>`).join("")}</select></label></div>
      <label class="f" style="margin-top:8px"><span>同封物（送付状のチェックリストに載ります）</span></label><div class="checks" id="mitems"></div>
      <div class="row" style="margin-top:6px"><input class="i" id="mitem" placeholder="同封物を追加" style="flex:1"><button class="btn s" id="mitemb">追加</button></div>
      <label class="f" style="margin-top:8px"><span>送付状に添える一言（任意）</span><input class="i" id="mnote" maxlength="200"></label>
      <div class="row" style="margin-top:10px"><button class="btn p" id="mcreate">作成（照合待ちにする）</button></div><div class="msgline" id="mm"></div></div></div>
    <div><div class="card"><h2><i class="fa-solid fa-list-check"></i>作成した送付物 <small>作成 → 印刷 → 別の担当者が照合 → 発送</small></h2>
      <div class="row" style="margin-bottom:8px"><button class="btn s" data-pdf="letter"><i class="fa-regular fa-file-pdf"></i>送付状</button><button class="btn s" data-pdf="envelope"><i class="fa-regular fa-envelope"></i>封筒</button>
        <button class="btn s" data-pdf="labels"><i class="fa-solid fa-tags"></i>宛名ラベル（A4 12面）</button><label class="checks" title="AIが作った文面を含む場合に付けます（注意点2(3)③）"><label><input type="checkbox" id="mai">AI表記を付ける</label></label></div>
      <div id="mlist"></div></div></div></div>`;
  let picked = new Map();
  const renderItems = () => { $("#mitems").innerHTML = META.doc_kinds[$("#mkind").value].map(i => `<label><input type="checkbox" checked value="${esc(i)}">${esc(i)}</label>`).join(""); };
  $("#mkind").onchange = renderItems; renderItems();
  $("#mitemb").onclick = () => { const v = $("#mitem").value.trim(); if (v) { $("#mitems").insertAdjacentHTML("beforeend", `<label><input type="checkbox" checked value="${esc(v)}">${esc(v)}</label>`); $("#mitem").value = ""; } };
  const showPicked = () => $("#mpicked").textContent = "選んだ宛先：" + (picked.size ? [...picked.values()].join("、") : "なし");
  const find = async () => {
    const rs = await api("/api/customers?q=" + encodeURIComponent($("#mq").value));
    $("#mres").innerHTML = Array.isArray(rs) && rs.length ? `<div class="tbl"><table>${rs.slice(0, 20).map(c => `<tr><td><input type="checkbox" data-id="${c.id}" data-n="${esc(c.name)}" ${picked.has(c.id) ? "checked" : ""}></td><td>${esc(c.name)}</td><td>〒${esc(c.postal)} ${esc(c.address)}</td></tr>`).join("")}</table></div>` : `<div class="empty">該当なし</div>`;
    $("#mres").querySelectorAll("input").forEach(i => i.onchange = () => { i.checked ? picked.set(+i.dataset.id, i.dataset.n) : picked.delete(+i.dataset.id); showPicked(); });
  };
  $("#mqb").onclick = find; $("#mq").onkeydown = e => { if (e.key === "Enter") find(); }; find();
  $("#mcreate").onclick = async () => {
    const r = await api("/api/mailing/create", {customer_ids: [...picked.keys()], doc_kind: $("#mkind").value, envelope: $("#menv").value,
      items: [...$("#mitems").querySelectorAll("input:checked")].map(i => i.value), note: $("#mnote").value});
    say($("#mm"), r, `${r.ids?.length || 0}件を作成しました。右の一覧で選んで印刷してください`);
    if (r.ok) { picked.clear(); showPicked(); MSEL = new Set(r.ids); list(); find(); }
  };
  el.querySelectorAll("[data-pdf]").forEach(b => b.onclick = () => {
    if (!MSEL.size) return alert("右の一覧で印刷するものを選んでください");
    openFile(`/api/mailing/pdf?what=${b.dataset.pdf}&ids=${[...MSEL].join(",")}&ai=${$("#mai").checked ? 1 : 0}`);
  });
  const list = async () => {
    const rs = await api("/api/mailing");
    if (!Array.isArray(rs)) return;
    const tag = s => `<span class="tag ${s === "発送済" ? "ok" : s === "確認済" ? "" : "am"}">${esc(s)}</span>`;
    $("#mlist").innerHTML = rs.length ? `<div class="tbl"><table><tr><th></th><th>No.</th><th>宛先</th><th>種類</th><th>作成</th><th>照合</th><th>状態</th><th></th></tr>${rs.map(m => `<tr>
      <td><input type="checkbox" data-id="${m.id}" ${MSEL.has(m.id) ? "checked" : ""}></td><td>${m.id}</td><td>${esc(m.customer_name)}</td><td>${esc(m.doc_kind)}<br><small class="time">${esc(m.envelope)}</small></td>
      <td class="nw">${esc(m.created_name || "")}<br><small class="time">${esc((m.created_at || "").slice(5, 16))}</small></td><td class="nw">${esc(m.checked_name || "")}</td><td>${tag(m.status)}</td>
      <td class="nw">${m.status === "確認待ち" ? `<button class="btn s" data-chk="${m.id}" ${m.created_by === ME.id ? 'disabled title="作成者とは別の担当者が照合します"' : ""}>照合した</button>` : m.status === "確認済" ? `<button class="btn s" data-sent="${m.id}">発送済にする</button>` : ""}</td></tr>`).join("")}</table></div>`
      : `<div class="empty">まだありません</div>`;
    $("#mlist").querySelectorAll("input[data-id]").forEach(i => i.onchange = () => { i.checked ? MSEL.add(+i.dataset.id) : MSEL.delete(+i.dataset.id); });
    $("#mlist").querySelectorAll("[data-chk]").forEach(b => b.onclick = async () => {
      if (!confirm("宛名・同封物・送付状の内容を、印刷物で照合しましたか？")) return;
      const r = await api("/api/mailing/check", {id: +b.dataset.chk}); if (r.error) alert(r.error); list();
    });
    $("#mlist").querySelectorAll("[data-sent]").forEach(b => b.onclick = async () => { const r = await api("/api/mailing/sent", {id: +b.dataset.sent}); if (r.error) alert(r.error); list(); });
  };
  list();
};

// =================== 見積依頼書 ===================
let QSEL = null;
LOAD.quotes = async () => {
  const el = $("#p-quotes");
  if (!purposeGate(el, ["name"], "見積依頼書")) return;  // 契約者名などの顧客情報を使う（F-02）
  el.innerHTML = `<div class="cols"><div><div class="card"><h2><i class="fa-solid fa-wand-magic-sparkles"></i>依頼文から下書き</h2>
      <p class="hint">営業からの依頼文を貼ると、AIが見積依頼書の項目に分けます（推測で補いません）。足りない項目は「確認の質問」にまとめます。</p>
      <div class="row"><select class="i" id="qkind" style="width:auto"><option value="">種目は自動で判定</option>${META.quote_kinds.map(k => `<option>${esc(k)}</option>`).join("")}</select></div>
      <textarea class="i" id="qtext" style="margin-top:8px" placeholder="例：架空一郎さん、12月1日始期でプリウス（ZVW30）の自動車保険の見積をお願いします。家族限定、35歳以上、車両保険は一般で。"></textarea>
      <div class="row" style="margin-top:8px"><button class="btn p" id="qdraft">下書きを作る</button></div><div class="msgline" id="qm"></div></div>
      <div class="card"><h2><i class="fa-solid fa-list"></i>見積依頼書の一覧</h2><div id="qlist"></div></div></div>
    <div><div class="card" id="qdetail"><div class="empty">一覧から選ぶか、下書きを作ってください</div></div></div></div>`;
  $("#qdraft").onclick = async () => {
    $("#qm").className = "msgline"; $("#qm").textContent = "AIが項目に分けています…";
    const r = await api("/api/quotes/draft", {text: $("#qtext").value, kind: $("#qkind").value});
    if (r.error) return say($("#qm"), r);
    say($("#qm"), {}, r.note); QSEL = r.id; qlist(); qdetail(r);
  };
  qlist();
  if (QSEL) qdetail(await api("/api/quote?id=" + QSEL));
};
async function qlist() {
  const rs = await api("/api/quotes");
  if (!Array.isArray(rs)) return;
  const tag = s => `<span class="tag ${s === "成約" || s === "突合済" ? "ok" : s === "差異あり" ? "ng" : s === "下書き" ? "am" : s === "不成約" ? "gr" : ""}">${esc(s)}</span>`;
  $("#qlist").innerHTML = rs.length ? `<div class="tbl"><table><tr><th>No.</th><th>種目</th><th>契約者</th><th>状態</th><th>作成</th></tr>${rs.map(q => `<tr class="click ${q.id === QSEL ? "sel" : ""}" data-id="${q.id}">
    <td>${q.id}</td><td>${esc(q.kind)}</td><td>${esc(q.contractor)}</td><td>${tag(q.status)}</td><td class="nw">${esc(q.created_name || "")}<br><small class="time">${esc((q.created_at || "").slice(0, 16))}</small></td></tr>`).join("")}</table></div>` : `<div class="empty">まだありません</div>`;
  $("#qlist").querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = async () => { QSEL = +tr.dataset.id; qlist(); qdetail(await api("/api/quote?id=" + QSEL)); });
}
function qdetail(q) {
  if (q.error) return $("#qdetail").innerHTML = `<div class="empty">${esc(q.error)}</div>`;
  const lock = ["成約", "不成約", "突合済"].includes(q.status) ? "readonly" : "";
  $("#qdetail").innerHTML = `<h2><i class="fa-regular fa-file-lines"></i>見積依頼 No.${q.id}（${esc(q.kind)}） <span class="tag">${esc(q.status)}</span></h2>
    <p class="hint">作成：${esc(q.created_name || "")}　承認：${esc(q.approved_name || "—")}</p>
    <form id="qf">${q.template.map(f => `<label class="f ${f.required ? "req" : ""}" style="margin-bottom:8px"><span>${esc(f.label)}</span>
      <input class="i ${f.required && !q.fields[f.key] ? "miss" : ""}" name="${esc(f.key)}" value="${esc(q.fields[f.key] || "")}" ${lock}></label>`).join("")}
    <div class="row">${lock ? "" : `<button class="btn">保存</button><button type="button" class="btn p" id="qapp" ${q.missing.length ? "disabled" : ""}>確認して承認</button>`}
      ${["承認済", "突合済", "差異あり", "成約"].includes(q.status) ? `<button type="button" class="btn" id="qsheet"><i class="fa-regular fa-file-pdf"></i>J-NET入力票</button>` : ""}</div>
    <div class="msgline" id="qdm"></div></form>
    ${q.missing.length ? `<div class="card" style="margin-top:12px;background:#fffbeb"><h2>確認の質問 <small>社内チャットのスレッドに貼って営業に確認</small></h2><div class="pre" id="qques">${esc(q.questions)}</div>
      <button class="btn s" id="qcopy" style="margin-top:6px"><i class="fa-regular fa-copy"></i>コピー</button></div>` : ""}
    ${["承認済", "突合済", "差異あり"].includes(q.status) ? `<div class="card" style="margin-top:12px"><h2>J-NETの結果と突合 <small>J-NETへは人が手入力。保存した見積書の文字を貼る</small></h2>
      <textarea class="i" id="qjnet" placeholder="J-NETの見積結果（見積書PDFの文字など）を貼り付け"></textarea><div class="row" style="margin-top:6px"><button class="btn" id="qrec">突合する</button></div>
      ${q.reconcile ? `<div class="tbl" style="margin-top:8px"><table><tr><th>項目</th><th>入力票の値</th><th>結果</th></tr>${q.reconcile.map(x => `<tr><td>${esc(x.label)}</td><td>${esc(x.value)}</td><td>${x.ok ? `<span class="tag ok">一致</span>` : `<span class="tag ng">見つからない（要確認）</span>`}</td></tr>`).join("")}</table></div>` : ""}</div>` : ""}
    ${["突合済", "差異あり", "承認済"].includes(q.status) ? `<div class="row" style="margin-top:12px"><input class="i" id="qcopyid" placeholder="成約時：申込書控えの文書ID" style="width:220px"><button class="btn" id="qwon">成約</button><button class="btn" id="qlost">不成約</button></div>
      <p class="hint" style="margin-top:6px">成約は申込書控えの保存期間に、不成約は作成から1年で自動削除（N-16）。</p>` : ""}`;
  const fields = () => Object.fromEntries(new FormData($("#qf")));
  $("#qf").onsubmit = async e => { e.preventDefault(); const r = await api("/api/quotes/update", {id: q.id, fields: fields()}); if (r.error) return say($("#qdm"), r); qdetail(r); say($("#qdm"), {}, "保存しました"); qlist(); };
  $("#qapp")?.addEventListener("click", async () => {
    const s = await api("/api/quotes/update", {id: q.id, fields: fields()});
    if (s.error) return say($("#qdm"), s);
    const r = await api("/api/quotes/approve", {id: q.id}); if (r.error) return say($("#qdm"), r); qdetail(r); qlist();
  });
  $("#qsheet")?.addEventListener("click", () => openFile("/api/quotes/sheet?id=" + q.id));
  $("#qcopy")?.addEventListener("click", () => navigator.clipboard.writeText(q.questions).then(() => $("#qcopy").textContent = "コピーしました"));
  $("#qrec")?.addEventListener("click", async () => { const r = await api("/api/quotes/reconcile", {id: q.id, text: $("#qjnet").value}); if (r.error) return alert(r.error); qdetail(r); qlist(); });
  $("#qwon")?.addEventListener("click", async () => { const r = await api("/api/quotes/close", {id: q.id, won: true, copy_id: $("#qcopyid").value}); if (r.error) return alert(r.error); qdetail(r); qlist(); });
  $("#qlost")?.addEventListener("click", async () => { if (!confirm("不成約にしますか？")) return; const r = await api("/api/quotes/close", {id: q.id, won: false}); qdetail(r); qlist(); });
}

// =================== タスク ===================
let STAFF = [];
LOAD.tasks = async () => {
  const el = $("#p-tasks");
  STAFF = await api("/api/staff/names");
  const r = await api("/api/tasks");
  if (r.error) return el.innerHTML = `<div class="empty">${esc(r.error)}</div>`;
  const opt = sel => `<option value="">担当未定</option>` + STAFF.map(s => `<option value="${s.id}" ${s.id === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  el.innerHTML = `<div class="stat"><div><b>${r.summary.open}</b><span>未完了</span></div><div><b class="${r.summary.overdue ? "ng-t" : ""}">${r.summary.overdue}</b><span>期限超過</span></div>
      <div><b>${r.summary.today}</b><span>今日まで</span></div><div><b>${r.summary.unassigned}</b><span>担当未定</span></div></div>
    <div class="card"><h2><i class="fa-solid fa-plus"></i>タスクを登録</h2>
      <div class="row"><textarea class="i" id="tsrc" placeholder="依頼文を貼ると、AIが件名・担当・期限の案を作ります（例：山田さん、架空様の証券再発行の書類を金曜までに送ってください）" style="flex:1;min-height:56px"></textarea><button class="btn" id="tprop">案を作る</button></div>
      <div class="grid3" style="margin-top:8px"><label class="f req"><span>件名</span><input class="i" id="ttitle"></label><label class="f"><span>担当</span><select class="i" id="tas">${opt()}</select></label>
      <label class="f"><span>期限</span><input class="i" type="date" id="tdue"></label></div>
      <label class="f" style="margin-top:8px"><span>詳細</span><textarea class="i" id="tdet" style="min-height:50px"></textarea></label>
      <div class="row" style="margin-top:8px"><button class="btn p" id="tadd">登録（人が確定）</button></div><div class="msgline" id="tm"></div></div>
    <div class="board">${META.task_statuses.map(s => `<div class="lane"><h3>${esc(s)}<span>${r.tasks.filter(t => t.status === s).length}</span></h3>
      ${r.tasks.filter(t => t.status === s).map(t => `<div class="tcard ${t.overdue ? "over" : ""}"><b>No.${t.id} ${esc(t.title)}</b>
        <small>${esc(t.assignee_name || "担当未定")}${t.due ? "・期限 " + esc(t.due) : ""}${t.overdue ? `　<span class="tag ng">期限超過</span>` : ""}</small>
        ${t.detail ? `<div class="pre" style="margin-top:4px;color:var(--sub)">${esc(t.detail.slice(0, 140))}</div>` : ""}
        <div class="row">${META.task_statuses.filter(x => x !== s).map(x => `<button class="btn s" data-t="${t.id}" data-s="${esc(x)}">${esc(x)}へ</button>`).join("")}
        <select class="i" data-as="${t.id}" style="width:auto;padding:3px 6px;font-size:12px">${opt(t.assignee)}</select></div></div>`).join("")}</div>`).join("")}</div>`;
  $("#tprop").onclick = async () => {
    $("#tm").className = "msgline"; $("#tm").textContent = "AIが案を作っています…";
    const p = await api("/api/tasks/propose", {text: $("#tsrc").value});
    if (p.error) return say($("#tm"), p);
    $("#ttitle").value = p.title; $("#tas").value = p.assignee || ""; $("#tdue").value = p.due; $("#tdet").value = p.detail; say($("#tm"), {}, p.note);
  };
  $("#tadd").onclick = async () => {
    const x = await api("/api/tasks/create", {title: $("#ttitle").value, assignee: +$("#tas").value || null, due: $("#tdue").value, detail: $("#tdet").value});
    if (x.error) return say($("#tm"), x);
    LOAD.tasks(); counts();
  };
  el.querySelectorAll("[data-t]").forEach(b => b.onclick = async () => { await api("/api/tasks/update", {id: +b.dataset.t, status: b.dataset.s}); LOAD.tasks(); counts(); });
  el.querySelectorAll("[data-as]").forEach(s => s.onchange = async () => { await api("/api/tasks/update", {id: +s.dataset.as, assignee: +s.value || null}); LOAD.tasks(); });
};

// =================== 登録文書 ===================
LOAD.docs = async () => {
  const el = $("#p-docs"), adm = can("docs.approve");
  el.innerHTML = `<div class="card"><h2><i class="fa-solid fa-cloud-arrow-up"></i>文書を登録 <small>登録した版は「承認待ち」になり、管理者が承認すると検索の対象になります</small></h2>
      <label class="drop" id="drop"><input type="file" id="upfile" accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.heic,.tif,.tiff" hidden>
        <i class="fa-solid fa-cloud-arrow-up"></i><b id="upname">ここにファイルをドラッグ、またはクリックして選択</b>
        <small>PDF（文字のPDF・スキャンした冊子）／テキスト（.txt .md）／写真・画像（端末内で文字を読み取り）</small></label>
      <div class="grid3" style="margin-top:10px"><label class="f"><span>文書名（同じ名前で登録すると新しい版）</span><input class="i" id="uptitle"></label>
        <label class="f"><span>版（例：2026年4月版）</span><input class="i" id="upver"></label>
        <label class="f"><span>保険会社・発行元</span><input class="i" id="upins" list="insurers" placeholder="損保ジャパン／社内 など"><datalist id="insurers"><option>損保ジャパン</option><option>社内</option></datalist></label></div>
      <div class="row" style="margin-top:8px"><button class="btn p" id="upbtn" disabled><i class="fa-solid fa-plus"></i>登録（承認待ちにする）</button></div>
      <div class="msgline" id="upmsg">お客さまの情報を含む資料は登録しないでください（マイナンバー・カード番号らしき番号があれば自動で止めます）。</div>
      ${adm ? `<p class="hint" style="margin-top:10px"><i class="fa-regular fa-folder-open"></i> フォルダ監視：<span class="mono" id="inbox">data/inbox</span> に置いたファイル（「文書名__版.pdf」の名前なら版も読み取り）を1分ごとに確認し、新しいもの・変わったものを承認待ちで取り込みます。
        <button class="btn s" id="scan">今すぐ確認</button></p>` : ""}</div>
    <div class="card"><h2><i class="fa-solid fa-book"></i>文書と版</h2><div id="dlist"></div></div>`;
  let picked = null;
  const pick = f => { picked = f; $("#upname").textContent = f ? `${f.name}（${(f.size / 1024 / 1024).toFixed(1)}MB）` : "ここにファイルをドラッグ、またはクリックして選択";
    if (f && !$("#uptitle").value) $("#uptitle").value = f.name.replace(/\.[^.]+$/, ""); $("#upbtn").disabled = !f; };
  $("#upfile").onchange = () => pick($("#upfile").files[0]);
  const drop = $("#drop");
  ["dragover", "dragenter"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", ev => pick(ev.dataTransfer.files[0]));
  $("#upbtn").onclick = async () => {
    $("#upbtn").disabled = true; $("#upmsg").className = "msgline"; $("#upmsg").textContent = "取り込み中…（文字の読み取り → 分割 → 検索用の索引づくり）";
    const r = await upload("/api/docs/upload", picked, {"X-Filename": picked.name, "X-Title": $("#uptitle").value, "X-Version": $("#upver").value, "X-Insurer": $("#upins").value});
    if (r.error) { say($("#upmsg"), r); $("#upbtn").disabled = false; return; }
    say($("#upmsg"), {}, `登録しました：「${r.title}」${r.kind}・${r.sections}件に分割。管理者の承認後に検索できます。`);
    $("#uptitle").value = $("#upver").value = $("#upins").value = ""; pick(null); list(); counts();
  };
  $("#scan")?.addEventListener("click", async () => { const r = await api("/api/docs/scan", {}); $("#inbox").textContent = r.folder || ""; alert(`${r.added ?? 0}件を承認待ちで取り込みました`); list(); counts(); });
  const list = async () => {
    const rs = await api("/api/docs");
    if (!Array.isArray(rs)) return;
    const tag = s => `<span class="tag ${s === "active" ? "ok" : s === "pending" ? "am" : "gr"}">${esc(META && {pending: "承認待ち", active: "検索対象", retired: "旧版", rejected: "差し戻し"}[s])}</span>`;
    $("#dlist").innerHTML = `<div class="tbl"><table><tr><th>文書名</th><th>版</th><th>発行元</th><th>状態</th><th>件数</th><th>取り込み元</th><th>登録・承認</th><th></th></tr>${rs.map(v => `<tr>
      <td>${esc(v.title)}</td><td class="nw">${esc(v.version)}</td><td>${esc(v.insurer || "")}</td><td>${tag(v.status)}</td><td>${v.articles}</td><td><small>${esc(v.source)}</small></td>
      <td class="nw"><small>${esc(v.uploaded_name || "—")} ${esc((v.created_at || "").slice(0, 10))}<br>承認：${esc(v.approved_name || (v.status === "active" ? "初期登録" : "—"))}</small></td>
      <td class="nw"><button class="btn s" data-pv="${v.id}">中身</button>
        ${adm && v.status === "pending" ? `<button class="btn s" data-ed="${v.id}">修正</button><button class="btn s p" data-ap="${v.id}">承認</button><button class="btn s d" data-rj="${v.id}">差し戻し</button>` : ""}
        ${can("docs.delete") && v.status === "active" ? `<button class="btn s d" data-rm="${v.id}" title="検索の対象から外す">外す</button>` : ""}</td></tr>`).join("")}</table></div>`;
    const on = (sel, fn) => $("#dlist").querySelectorAll(sel).forEach(b => b.onclick = () => fn(+Object.values(b.dataset)[0], rs.find(x => x.id === +Object.values(b.dataset)[0])));
    on("[data-pv]", async id => { const cs = await api("/api/docs/preview?id=" + id); modal(`<h2>取り込んだ中身（先頭200件）</h2>${cs.map(c => `<div class="src"><b>${esc(c.article)}</b><br>${esc(c.body)}</div>`).join("")}<div class="row end"><button class="btn" data-close>閉じる</button></div>`); });
    on("[data-ap]", async (id, v) => { if (!confirm(`「${v.title}」${v.version} を承認して検索の対象にしますか？${rs.some(x => x.title === v.title && x.status === "active") ? "\n今の検索対象の版は「旧版」になり、検索から外れます。" : ""}`)) return; const r = await api("/api/docs/approve", {id}); if (r.error) alert(r.error); list(); counts(); });
    on("[data-rj]", async id => { if (!confirm("差し戻しますか？（索引と原本を消します）")) return; await api("/api/docs/reject", {id}); list(); counts(); });
    on("[data-rm]", async (id, v) => { if (!confirm(`「${v.title}」を検索の対象から外しますか？`)) return; await api("/api/docs/remove", {id}); list(); });
    on("[data-ed]", (id, v) => {
      const box = modal(`<h2>承認前の修正</h2><label class="f"><span>文書名</span><input class="i" id="et" value="${esc(v.title)}"></label>
        <label class="f" style="margin-top:8px"><span>版</span><input class="i" id="ev" value="${esc(v.version)}"></label><label class="f" style="margin-top:8px"><span>発行元</span><input class="i" id="ei" value="${esc(v.insurer || "")}"></label>
        <div class="row end" style="margin-top:12px"><button class="btn" data-close>やめる</button><button class="btn p" id="es">保存</button></div>`);
      box.querySelector("#es").onclick = async () => { await api("/api/docs/edit", {id, title: $("#et").value, version: $("#ev").value, insurer: $("#ei").value}); closeModal(); list(); };
    });
  };
  list();
};

// =================== 文書保管庫 ===================
LOAD.vault = async () => {
  const el = $("#p-vault"), adm = can("vault.approve"), a = allowed();
  el.innerHTML = `<div class="card"><h2><i class="fa-solid fa-lock"></i>申込書控えを登録 <small>暗号化して保管。AIは読みません</small></h2>
      <p class="hint">保存期間：${Object.entries(META.retention).map(([k, v]) => `${k}${v}年`).join("・")}（起算日＝登録日。システムが自動で付け、変更できません）。1通に複数の種目があれば最も長い期間。<b>種目を選ばないと「要判断」になり、削除されません</b>。</p>
      <div class="grid3"><label class="f"><span>ファイル（PDF・画像）</span><input class="i" type="file" id="vfile" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.heic"></label>
        <label class="f"><span>書類名</span><input class="i" id="vtitle" placeholder="例：自動車保険 申込書控え"></label>
        <label class="f"><span>顧客ID${a && a.includes("name") ? "" : "（顧客DBの番号）"}</span><input class="i" id="vcust" inputmode="numeric" placeholder="任意"></label></div>
      <div class="row" style="margin-top:8px"><span class="hint" style="margin:0">種目（必須）：</span><div class="checks" id="vkinds">${META.kinds.map(k => `<label><input type="checkbox" value="${esc(k)}">${esc(k)}</label>`).join("")}</div>
        <span class="sp"></span><button class="btn p" id="vadd">登録</button></div><div class="msgline" id="vm"></div></div>
    ${adm ? `<div class="card"><h2><i class="fa-solid fa-stamp"></i>承認待ちの申請 <small>申請者と承認者は別の人にします</small></h2><div id="vreq"></div></div>` : ""}
    <div class="card"><h2><i class="fa-solid fa-box-archive"></i>保管中の申込書控え</h2><div class="row" style="margin-bottom:8px"><input class="i" id="vq" placeholder="書類名・顧客・文書IDで絞り込み" style="flex:1"><button class="btn" id="vqb">絞り込み</button></div><div id="vlist"></div></div>
    ${adm ? `<div class="card"><h2><i class="fa-solid fa-gears"></i>自動削除と点検</h2>
      <p class="hint">日次ジョブ（午前2:00）：満了14日前・前日に削除予定を社内通知 → 満了したものを暗号化消去（鍵を破棄）→ 残存チェック → 削除ログ（日時・文書ID・実行者IDのみ）。PCが止まっていた日は、次の起動時に追いつきます。</p>
      <div class="row"><button class="btn" id="vrun">日次ジョブを今すぐ実行</button><span class="hint" style="margin:0" id="vlast"></span><span class="sp"></span>
        <input class="i" type="month" id="vmonth" style="width:auto"><button class="btn" id="vrec">件数を照合</button></div><div class="msgline" id="vjm"></div>
      <details><summary>削除ログ・種目変更ログ</summary><div id="vlogs"></div></details></div>` : ""}`;
  $("#vadd").onclick = async () => {
    const f = $("#vfile").files[0];
    if (!f) return say($("#vm"), {error: "ファイルを選んでください"});
    const kinds = [...$("#vkinds").querySelectorAll("input:checked")].map(i => i.value);
    if (!kinds.length && !confirm("種目が選ばれていません。「要判断（未設定）」として登録し、種目を設定するまで削除しません。よろしいですか？")) return;
    const r = await upload("/api/vault/upload", f, {"X-Filename": f.name, "X-Title": $("#vtitle").value, "X-Kinds": kinds.join(","), "X-Customer": $("#vcust").value});
    say($("#vm"), r, `登録しました：文書ID ${r.id}／登録日 ${r.registered}／満了日 ${r.expires || "なし（要判断）"}`);
    if (r.ok) { $("#vfile").value = ""; $("#vtitle").value = ""; $("#vcust").value = ""; $("#vkinds").querySelectorAll("input").forEach(i => i.checked = false); list(); }
  };
  const list = async () => {
    const rs = await api("/api/vault?q=" + encodeURIComponent($("#vq").value));
    if (!Array.isArray(rs)) return;
    const st = r => `<span class="tag ${r.legal_hold ? "h" : r.kinds === "未設定" ? "am" : r.days_left !== null && r.days_left <= 14 ? "am" : "ok"}">${esc(r.state)}</span>${r.pending ? ` <span class="tag">申請中</span>` : ""}`;
    $("#vlist").innerHTML = rs.length ? `<div class="tbl"><table><tr><th>文書ID</th><th>書類名</th><th>顧客</th><th>種目</th><th>登録日</th><th>満了日</th><th>残り</th><th>状態</th><th></th></tr>${rs.map(r => `<tr>
      <td>${r.id}</td><td>${esc(r.title)}</td><td>${esc(r.customer)}</td><td>${esc(r.kinds)}</td><td class="nw">${esc(r.registered)}</td><td class="nw">${esc(r.expires || "—")}</td>
      <td class="nw">${r.days_left === null ? "—" : r.days_left + "日"}</td><td>${st(r)}${r.legal_hold ? `<br><small class="time">${esc(r.hold_reason)} 見直し ${esc(r.hold_review)}</small>` : ""}</td>
      <td class="nw">${can("vault.view") ? `<button class="btn s" data-view="${r.id}">表示</button>` : ""}<button class="btn s" data-kind="${r.id}">種目変更</button>
        <button class="btn s" data-hold="${r.id}">${r.legal_hold ? "ホールド解除" : "ホールド"}</button></td></tr>`).join("")}</table></div>` : `<div class="empty">保管中の書類はありません</div>`;
    $("#vlist").querySelectorAll("[data-view]").forEach(b => b.onclick = () => openFile("/api/vault/file?id=" + b.dataset.view));
    $("#vlist").querySelectorAll("[data-kind]").forEach(b => b.onclick = () => {
      const r = rs.find(x => x.id === +b.dataset.kind);
      const box = modal(`<h2>種目変更の申請（文書ID ${r.id}）</h2><p class="hint">今の種目：${esc(r.kinds)}／登録日 ${esc(r.registered)}。承認されると、満了日を<b>元の登録日から</b>計算し直し、変更ログに残します。</p>
        <div class="checks">${META.kinds.map(k => `<label><input type="checkbox" value="${esc(k)}">${esc(k)}</label>`).join("")}</div>
        <div class="row end" style="margin-top:12px"><button class="btn" data-close>やめる</button><button class="btn p" id="kreq">申請</button></div><div class="msgline" id="km"></div>`);
      box.querySelector("#kreq").onclick = async () => { const x = await api("/api/vault/request", {copy_id: r.id, type: "kind", kinds: [...box.querySelectorAll("input:checked")].map(i => i.value)}); if (x.error) return say($("#km"), x); closeModal(); list(); counts(); reqs(); };
    });
    $("#vlist").querySelectorAll("[data-hold]").forEach(b => b.onclick = async () => {
      const r = rs.find(x => x.id === +b.dataset.hold);
      if (r.legal_hold) { if (!confirm("ホールドの解除を申請しますか？（承認後、満了していれば次の日次ジョブで削除されます）")) return; const x = await api("/api/vault/request", {copy_id: r.id, type: "unhold"}); if (x.error) alert(x.error); list(); reqs(); return; }
      const box = modal(`<h2>リーガルホールドの申請（文書ID ${r.id}）</h2><p class="hint">紛争中・事故対応中などで、満了後も残す必要があるときに申請します。生成AI責任者（管理者）が承認した場合だけ削除を保留し、6か月ごとに見直します。</p>
        <select class="i" id="hr">${Object.entries(META.hold_reasons).map(([k, v]) => `<option value="${k}">${k}：${esc(v)}</option>`).join("")}</select>
        <div class="row end" style="margin-top:12px"><button class="btn" data-close>やめる</button><button class="btn p" id="hreq">申請</button></div><div class="msgline" id="hm"></div>`);
      box.querySelector("#hreq").onclick = async () => { const x = await api("/api/vault/request", {copy_id: r.id, type: "hold", reason: $("#hr").value}); if (x.error) return say($("#hm"), x); closeModal(); list(); counts(); reqs(); };
    });
  };
  const reqs = async () => {
    if (!adm) return;
    const rs = await api("/api/vault/requests");
    if (!Array.isArray(rs)) return;
    const what = q => q.type === "kind" ? `種目変更：${esc(q.current_kinds)} → ${esc(q.payload.kinds.join(","))}` : q.type === "hold" ? `ホールド：${esc(q.payload.reason)} ${esc(META.hold_reasons[q.payload.reason] || "")}` : "ホールド解除";
    $("#vreq").innerHTML = rs.length ? `<div class="tbl"><table><tr><th>申請No.</th><th>文書ID</th><th>内容</th><th>申請者</th><th>日時</th><th></th></tr>${rs.map(q => `<tr><td>${q.id}</td><td>${q.copy_id}</td><td>${what(q)}</td><td>${esc(q.requested_name)}</td><td class="nw">${esc(q.ts)}</td>
      <td class="nw">${q.requested_by === ME.id ? `<small class="time">自分の申請は承認できません</small>` : `<button class="btn s p" data-ok="${q.id}">承認</button><button class="btn s d" data-ng="${q.id}">否認</button>`}</td></tr>`).join("")}</table></div>` : `<div class="empty">承認待ちの申請はありません</div>`;
    const dec = async (id, ok) => { const x = await api("/api/vault/decide", {id, approve: ok}); if (x.error) alert(x.error); reqs(); list(); counts(); };
    $("#vreq").querySelectorAll("[data-ok]").forEach(b => b.onclick = () => dec(+b.dataset.ok, true));
    $("#vreq").querySelectorAll("[data-ng]").forEach(b => b.onclick = () => dec(+b.dataset.ng, false));
  };
  const logs = async () => {
    if (!adm) return;
    const l = await api("/api/vault/logs");
    $("#vlast").textContent = "最終実行：" + (l.last_run || "まだ");
    $("#vlogs").innerHTML = `<h3 style="font-size:13px">削除ログ（日時・文書ID・実行者IDのみ）</h3><div class="tbl"><table><tr><th>日時</th><th>文書ID</th><th>満了日</th><th>実行者</th><th>ジョブ</th><th>残存チェック</th></tr>${l.deletion.map(d => `<tr><td>${esc(d.ts)}</td><td>${d.copy_id}</td><td>${esc(d.expires)}</td><td>${esc(d.executor)}</td><td class="mono">${esc(d.job_id)}</td><td>${d.check_ok ? '<span class="tag ok">OK</span>' : '<span class="tag ng">失敗</span>'}</td></tr>`).join("") || `<tr><td colspan="6">なし</td></tr>`}</table></div>
      <h3 style="font-size:13px">種目変更ログ</h3><div class="tbl"><table><tr><th>日時</th><th>文書ID</th><th>変更前</th><th>変更後</th><th>申請</th><th>承認</th></tr>${l.kind_change.map(k => `<tr><td>${esc(k.ts)}</td><td>${k.copy_id}</td><td>${esc(k.before)}</td><td>${esc(k.after)}</td><td>${esc(k.requested_name)}</td><td>${esc(k.approved_name)}</td></tr>`).join("") || `<tr><td colspan="6">なし</td></tr>`}</table></div>`;
  };
  $("#vqb").onclick = list; $("#vq").onkeydown = e => { if (e.key === "Enter") list(); };
  if (adm) {
    $("#vmonth").value = today().slice(0, 7);
    $("#vrun").onclick = async () => { if (!confirm("満了した申込書控えを今すぐ削除します。よろしいですか？")) return; const r = await api("/api/vault/run", {}); say($("#vjm"), r, `ジョブ ${r.job_id}：削除 ${r.deleted}件／失敗 ${r.failed}件／事前通知 ${r.notice}件／ホールド ${r.held}件／要判断 ${r.unset}件`); list(); logs(); };
    $("#vrec").onclick = async () => { const r = await api("/api/vault/reconcile?month=" + $("#vmonth").value); say($("#vjm"), r.ok ? {} : {error: "照合が合いません"}, ""); $("#vjm").textContent = `${r.month}：予定 ${r.scheduled}件 ＝ 削除 ${r.deleted}件 ＋ ホールド ${r.held}件 ＋ 満了前 ${r.waiting}件　（削除失敗 ${r.failed}件・削除漏れ ${r.leaked}件）→ ${r.ok ? "一致" : "不一致：確認してください"}`; };
  }
  list(); reqs(); logs();
};

// =================== 監査ログ ===================
const ACT = {login: "ログイン", login_fail: "ログイン失敗", logout: "ログアウト", setup: "初期設定", purpose: "利用目的の選択", password_change: "パスワード変更",
  chat: "社内チャット", line_classify: "LINE分類（試験）", line_in: "LINE受信", line_send: "LINE送信（担当者）", line_mode: "LINEモード切替", line_link: "LINE顧客ひも付け",
  line_attach_view: "LINE添付の閲覧", doc_register: "文書の登録", doc_approve: "文書の承認", doc_reject: "文書の差し戻し", doc_remove: "文書を検索から外す",
  customer_search: "顧客の検索", customer_view: "顧客の閲覧", customer_save: "顧客の登録・更新", customer_delete: "顧客の削除", customer_extract: "顧客の入力支援",
  vault_register: "申込書控えの登録", vault_view: "申込書控えの閲覧", vault_request: "保管庫の申請", vault_decide: "保管庫の承認・否認", vault_job: "日次削除ジョブ",
  mailing_create: "送付物の作成", mailing_print: "送付物の印刷", mailing_check: "送付物の照合", mailing_sent: "発送済", quote_draft: "見積依頼の下書き",
  quote_update: "見積依頼の修正", quote_approve: "見積依頼の承認", quote_print: "J-NET入力票の印刷", quote_reconcile: "J-NETとの突合", quote_close: "成約・不成約",
  task_create: "タスク登録", task_update: "タスク更新", task_propose: "タスク案の作成", staff_create: "社員の追加", staff_update: "社員の変更", ai_switch: "AI停止スイッチ",
  settings_sender: "差出人の設定"};
const act = a => ACT[a] ? `${ACT[a]}<br><small class="time">${esc(a)}</small>` : esc(a);
LOAD.audit = async () => {
  const el = $("#p-audit");
  const [v, rs] = await Promise.all([api("/api/audit/verify"), api("/api/audit?limit=300")]);
  el.innerHTML = `<div class="card" style="border-color:${v.ok ? "#bbf7d0" : "#fecaca"}"><h2>${v.ok ? '<i class="fa-solid fa-shield-halved ok-t"></i>改ざんは見つかりません' : '<i class="fa-solid fa-triangle-exclamation ng-t"></i>ログの改ざん・欠落を検出しました'}</h2>
      <p class="hint" style="margin:0">ハッシュチェーンを先頭から ${v.checked}件 検証${v.ok ? "しました" : `。最初に壊れている行：No.${v.broken_at}`}。記録するのは日時・社員・目的・操作・結果だけで、質問やメッセージの本文は保存しません。</p></div>
    <div class="tbl"><table><tr><th>No.</th><th>日時</th><th>社員</th><th>画面</th><th>操作</th><th>利用目的</th><th>結果</th><th>参照</th><th>ガード</th><th>時間</th></tr>${(Array.isArray(rs) ? rs : []).map(a => `<tr>
      <td>${a.id}</td><td class="nw">${esc(a.ts)}</td><td>${esc(a.staff_name || (a.staff_id ? "ID" + a.staff_id : "—"))}</td><td>${esc(a.channel)}</td><td>${act(a.action)}</td><td>${esc(a.purpose)}</td>
      <td>${esc(a.result)}</td><td><small>${esc(a.refs)}</small></td><td class="${a.guard ? "ng-t" : ""}">${esc(a.guard)}</td><td class="nw">${a.ms ? (a.ms / 1000).toFixed(1) + "秒" : ""}</td></tr>`).join("")}</table></div>`;
};

// =================== 月次点検 ===================
LOAD.monthly = async (month) => {
  const el = $("#p-monthly");
  const m = await api("/api/monthly?month=" + (month || today().slice(0, 7)));
  const blocked = m.blocked.reduce((s, x) => s + x.n, 0);
  el.innerHTML = `<div class="row" style="margin-bottom:12px"><input class="i" type="month" id="mmonth" value="${esc(m.month)}" style="width:auto"><button class="btn" id="mgo">表示</button><span class="sp"></span><button class="btn" data-print><i class="fa-solid fa-print"></i>印刷して記録に残す</button></div>
    <div class="stat"><div><b class="${blocked ? "ng-t" : "ok-t"}">${blocked}</b><span>禁止情報の入力（ガードで停止）</span></div>
      <div><b class="${m.chain.ok ? "ok-t" : "ng-t"}">${m.chain.ok ? "OK" : "NG"}</b><span>監査ログの改ざんチェック</span></div>
      <div><b class="${m.vault.ok ? "ok-t" : "ng-t"}">${m.vault.ok ? "一致" : "不一致"}</b><span>申込書控えの削除件数</span></div>
      <div><b class="${m.ai_enabled ? "" : "ng-t"}">${m.ai_enabled ? "稼働中" : "停止中"}</b><span>AI</span></div></div>
    <div class="cols"><div>
      <div class="card"><h2>禁止情報の入力（判定基準：フェーズ移行は0件）</h2>${m.blocked.length ? `<div class="tbl"><table><tr><th>画面</th><th>理由</th><th>件数</th></tr>${m.blocked.map(x => `<tr><td>${esc(x.channel)}</td><td>${esc(x.guard)}</td><td>${x.n}</td></tr>`).join("")}</table></div><p class="hint" style="margin-top:8px">ガードで止まった入力です（AIには送っていません）。誰の入力かは監査ログで確認し、必要なら研修でフォローします。</p>` : `<div class="empty">ありません</div>`}</div>
      <div class="card"><h2>申込書控えの削除の照合（${esc(m.month)}満了分）</h2><p class="pre">予定 ${m.vault.scheduled}件 ＝ 削除 ${m.vault.deleted}件 ＋ ホールド ${m.vault.held}件 ＋ 満了前 ${m.vault.waiting}件\n削除失敗 ${m.vault.failed}件／削除漏れ ${m.vault.leaked}件</p></div>
      <div class="card"><h2>操作の件数</h2><div class="tbl"><table><tr><th>操作</th><th>結果</th><th>件数</th></tr>${m.actions.map(x => `<tr><td>${act(x.action)}</td><td>${esc(x.result)}</td><td>${x.n}</td></tr>`).join("")}</table></div></div></div>
    <div><div class="card"><h2>権限の棚卸し（四半期ごと） <small>不要になったアカウントは無効にする</small></h2><div class="tbl"><table><tr><th>ID</th><th>氏名</th><th>ロール</th><th>多要素認証</th><th>状態</th><th>最終ログイン</th></tr>${m.staff.map(s => `<tr><td>${esc(s.login)}</td><td>${esc(s.name)}</td><td>${esc(s.role_name)}</td><td>${s.mfa_enabled ? "登録済" : '<span class="tag am">未登録</span>'}</td><td>${s.active ? "有効" : '<span class="tag gr">無効</span>'}</td><td class="nw">${esc(s.last_login || "—")}</td></tr>`).join("")}</table></div></div>
      <div class="card"><h2>点検の記録</h2><p class="hint">点検者：${esc(ME.name)}　点検日：${today()}<br>確認したこと：□ 禁止情報の入力　□ ログの改ざん　□ 削除件数　□ 権限　□ モデル・ソフトの更新（月次パッチ）</p></div></div></div>`;
  $("#mgo").onclick = () => LOAD.monthly($("#mmonth").value);
};

// =================== 社員・設定 ===================
LOAD.admin = async () => {
  const el = $("#p-admin");
  const [ss, h, snd] = await Promise.all([api("/api/staff"), fetch("/api/health").then(x => x.json()), api("/api/settings/sender")]);
  const roles = META.roles;
  el.innerHTML = `<div class="card" style="border-color:${h.ai_enabled ? "var(--bd)" : "#fcd34d"}"><h2><i class="fa-solid fa-power-off"></i>AI停止スイッチ <small>インシデントのときはすぐ止める（注意点6(3)）</small></h2>
      <p class="hint">止めると、検索・分類・下書きなどAIを使う処理がすべて止まります（LINEのAIモードは一律の定型文に切り替わり、お客さまを無言にしません）。一覧・登録・印刷など、AIを使わない業務は続けられます。</p>
      <div class="row"><b>${h.ai_enabled ? '<span class="ok-t">AIは稼働中です</span>' : '<span class="ng-t">AIは停止中です</span>'}</b><span class="sp"></span>
        ${h.ai_enabled ? `<button class="btn d" id="aioff"><i class="fa-solid fa-circle-stop"></i>AIを停止する</button>` : `<button class="btn p" id="aion">AIを再開する</button>`}</div></div>
    <div class="cols"><div class="card"><h2><i class="fa-solid fa-users"></i>社員アカウント</h2><div class="tbl"><table><tr><th>ID</th><th>氏名</th><th>ロール</th><th>多要素</th><th>状態</th><th>最終ログイン</th><th></th></tr>${ss.map(s => `<tr>
        <td>${esc(s.login)}</td><td>${esc(s.name)}</td><td><select class="i" data-role="${s.id}" style="padding:3px 6px">${Object.entries(roles).map(([k, v]) => `<option value="${k}" ${k === s.role ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></td>
        <td>${s.mfa_enabled ? "登録済" : '<span class="tag am">未登録</span>'}</td><td>${s.active ? "有効" : '<span class="tag gr">無効</span>'}</td><td class="nw"><small>${esc(s.last_login || "—")}</small></td>
        <td class="nw"><button class="btn s" data-pw="${s.id}">仮パスワード</button><button class="btn s" data-mfa="${s.id}">多要素を再登録</button><button class="btn s ${s.active ? "d" : ""}" data-act="${s.id}" data-v="${s.active ? 0 : 1}">${s.active ? "無効にする" : "有効にする"}</button></td></tr>`).join("")}</table></div>
        <div class="msgline" id="sm"></div>
        <h2 style="margin-top:14px">社員を追加</h2><div class="grid2"><label class="f"><span>ログインID</span><input class="i" id="nlogin"></label><label class="f"><span>氏名</span><input class="i" id="nname"></label>
          <label class="f"><span>ロール</span><select class="i" id="nrole">${Object.entries(roles).map(([k, v]) => `<option value="${k}" ${k === "clerk" ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          <label class="f"><span>仮パスワード（初回ログインで本人が変更）</span><input class="i" id="npw"></label></div>
        <div class="row" style="margin-top:8px"><button class="btn p" id="nadd">追加</button></div>
        <p class="hint" style="margin-top:8px">管理者＝生成AI責任者（承認・ログ点検・停止スイッチ）。募集人・事務員は日常業務。申込書控えの原本を開けるのは管理者と事務員です。</p></div>
      <div class="card"><h2><i class="fa-regular fa-building"></i>差出人（送付状・封筒に印字）</h2>
        ${[["agency_name", "代理店名"], ["agency_postal", "郵便番号"], ["agency_address", "住所"], ["agency_tel", "電話"], ["agency_staff", "担当者名（任意）"]].map(([k, l]) => `<label class="f" style="margin-bottom:8px"><span>${l}</span><input class="i" data-snd="${k}" value="${esc(snd[k] || "")}"></label>`).join("")}
        <div class="row"><button class="btn p" id="sndsave">保存</button></div><div class="msgline" id="sndm"></div></div></div>`;
  const upd = async b => { const r = await api("/api/staff/update", b); say($("#sm"), r, "変更しました"); LOAD.admin(); };
  el.querySelectorAll("[data-role]").forEach(s => s.onchange = () => upd({id: +s.dataset.role, role: s.value}));
  el.querySelectorAll("[data-act]").forEach(b => b.onclick = () => upd({id: +b.dataset.act, active: !!+b.dataset.v}));
  el.querySelectorAll("[data-mfa]").forEach(b => b.onclick = () => { if (confirm("認証アプリの登録を消し、次のログインで登録し直してもらいます。よろしいですか？")) upd({id: +b.dataset.mfa, reset_mfa: true}); });
  el.querySelectorAll("[data-pw]").forEach(b => b.onclick = () => { const p = prompt("仮パスワード（10文字以上・英字と数字）。本人に安全な方法で伝えてください"); if (p) upd({id: +b.dataset.pw, reset_pw: p}); });
  $("#nadd").onclick = async () => { const r = await api("/api/staff/create", {login: $("#nlogin").value, name: $("#nname").value, role: $("#nrole").value, password: $("#npw").value}); say($("#sm"), r, "追加しました"); if (r.ok) LOAD.admin(); };
  $("#aioff")?.addEventListener("click", async () => { if (!confirm("AIの利用を止めます。よろしいですか？")) return; await api("/api/ai/switch", {enabled: false}); health(); LOAD.admin(); });
  $("#aion")?.addEventListener("click", async () => { await api("/api/ai/switch", {enabled: true}); health(); LOAD.admin(); });
  $("#sndsave").onclick = async () => { const b = {}; el.querySelectorAll("[data-snd]").forEach(i => b[i.dataset.snd] = i.value); say($("#sndm"), await api("/api/settings/sender", b)); };
};

boot();
