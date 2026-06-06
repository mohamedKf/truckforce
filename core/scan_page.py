"""
scan_page.py — Mobile invoice scanner page (served by Django, no app).

The office prints/displays a QR code containing /scan/<token>/. Any phone
scans it → this page opens → photograph the paper invoice (multiple pages
supported) → the server merges the photos into one PDF, files it on
Cloudinary under finance_docs/<year>/<month>/, and records a
FinanceDocument row. Upload-only token auth: the token grants no read
access and can be regenerated at any time.
"""

SCAN_HTML = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>TruckForce — סריקת מסמכים</title>
<style>
  :root { --amber:#F5A623; --dark:#141414; --card:#1E1E1E; --text:#E8E0D0;
          --muted:#888; --border:#2A2A2A; --green:#4CAF50; --red:#FF6B6B; }
  * { box-sizing:border-box; margin:0; padding:0; font-family:-apple-system,
      "Segoe UI", Roboto, "Heebo", Arial, sans-serif; }
  body { background:var(--dark); color:var(--text); min-height:100vh;
         padding:16px; max-width:520px; margin:0 auto; }
  h1 { font-size:20px; margin:8px 0 2px; }
  h1 span { color:var(--amber); }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--border);
          border-radius:12px; padding:14px; margin-bottom:12px; }
  label { display:block; font-size:13px; color:var(--muted); margin:10px 0 4px; }
  input[type=date], input[type=text], input[type=number] {
      width:100%; padding:12px; border-radius:8px; border:1px solid var(--border);
      background:#2A2A2A; color:var(--text); font-size:15px; }
  .kind { display:flex; gap:8px; }
  .kind button { flex:1; padding:13px; border-radius:8px; font-size:15px;
      border:1px solid var(--border); background:#2A2A2A; color:var(--muted); }
  .kind button.on-income  { background:#163A1B; color:#7BE38A; border-color:#2E7D32; }
  .kind button.on-expense { background:#3A1616; color:#FF9B9B; border-color:#B23B3B; }
  .snap { width:100%; padding:16px; margin-top:4px; border-radius:10px;
      background:var(--amber); color:#1A1A1A; font-size:17px; font-weight:700;
      border:none; }
  .pages { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .pg { position:relative; width:72px; height:96px; border-radius:8px;
        overflow:hidden; border:1px solid var(--border); }
  .pg img { width:100%; height:100%; object-fit:cover; }
  .pg .x { position:absolute; top:2px; left:2px; background:rgba(0,0,0,.65);
           color:#fff; border:none; border-radius:50%; width:22px; height:22px;
           font-size:13px; }
  .send { width:100%; padding:16px; margin-top:14px; border-radius:10px;
      background:var(--green); color:#fff; font-size:17px; font-weight:700;
      border:none; }
  .send:disabled { background:#3A3A3A; color:#777; }
  .msg { text-align:center; padding:10px; border-radius:8px; margin-top:10px;
         font-size:14px; display:none; }
  .ok   { background:#163A1B; color:#7BE38A; }
  .err  { background:#3A1616; color:#FF9B9B; }
  .done { text-align:center; padding:40px 10px; }
  .done .big { font-size:52px; }
  .again { margin-top:18px; padding:14px 26px; border-radius:10px;
      background:var(--amber); color:#1A1A1A; font-size:16px; font-weight:700;
      border:none; }
  .foot { text-align:center; color:#555; font-size:11px; margin-top:18px; }
</style>
</head>
<body>
<div id="form-view">
  <h1>📄 סריקת מסמך — <span>TruckForce</span></h1>
  <div class="sub">צלם את החשבונית, בחר סוג ותאריך — והמסמך יתויק אוטומטית</div>

  <div class="card">
    <div class="kind">
      <button id="btn-income"  onclick="setKind('income')">📥 הכנסה</button>
      <button id="btn-expense" onclick="setKind('expense')">📤 הוצאה</button>
    </div>

    <label>תאריך המסמך</label>
    <input type="date" id="doc_date">

    <label>ספק / לקוח (לא חובה)</label>
    <input type="text" id="vendor" placeholder="שם העסק שעל המסמך">

    <label>ח.פ / ת.ז (לא חובה)</label>
    <input type="text" id="vtax" inputmode="numeric" placeholder="מספר העוסק שעל המסמך">

    <label>סכום (לא חובה)</label>
    <input type="number" id="amount" inputmode="decimal" placeholder="₪">
  </div>

  <div class="card">
    <button class="snap" onclick="document.getElementById('cam').click()">📷 צלם עמוד</button>
    <button class="snap" style="background:#2A2A2A;color:var(--text);margin-top:8px"
            onclick="document.getElementById('pick').click()">📁 בחר קובץ (PDF / תמונה)</button>
    <input id="cam"  type="file" accept="image/*" capture="environment" hidden>
    <input id="pick" type="file" accept="image/*,application/pdf" multiple hidden>
    <div class="pages" id="pages"></div>
  </div>

  <button class="send" id="send" disabled onclick="send()">שמור בארכיון ✓</button>
  <div class="msg err" id="err"></div>
  <div class="foot">Powered by TruckForce</div>
</div>

<div id="done-view" class="done" style="display:none">
  <div class="big">✅</div>
  <h1>המסמך נשמר בארכיון</h1>
  <div class="sub" id="done-sub"></div>
  <button class="again" onclick="reset()">📷 סרוק מסמך נוסף</button>
  <div class="foot">Powered by TruckForce</div>
</div>

<script>
const TOKEN = "__TOKEN__";
let kind = null;
let pages = [];   // File objects

document.getElementById('doc_date').valueAsDate = new Date();

function setKind(k) {
  kind = k;
  document.getElementById('btn-income').className  = k==='income'  ? 'on-income'  : '';
  document.getElementById('btn-expense').className = k==='expense' ? 'on-expense' : '';
  refresh();
}

for (const id of ['cam', 'pick']) {
  document.getElementById(id).addEventListener('change', e => {
    for (const f of e.target.files) pages.push(f);
    e.target.value = '';
    renderPages();
    refresh();
  });
}

function renderPages() {
  const box = document.getElementById('pages');
  box.innerHTML = '';
  pages.forEach((f, i) => {
    const d = document.createElement('div');
    d.className = 'pg';
    let img;
    if (f.type === 'application/pdf') {
      img = document.createElement('div');
      img.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;' +
                          'justify-content:center;font-size:30px;background:#2A2A2A';
      img.textContent = '📄';
    } else {
      img = document.createElement('img');
      img.src = URL.createObjectURL(f);
    }
    const x = document.createElement('button');
    x.className = 'x'; x.textContent = '✕';
    x.onclick = () => { pages.splice(i, 1); renderPages(); refresh(); };
    d.appendChild(img); d.appendChild(x); box.appendChild(d);
  });
}

function refresh() {
  document.getElementById('send').disabled = !(kind && pages.length > 0);
}

async function send() {
  const btn = document.getElementById('send');
  btn.disabled = true; btn.textContent = 'שולח...';
  const err = document.getElementById('err'); err.style.display = 'none';

  const fd = new FormData();
  fd.append('kind', kind);
  fd.append('doc_date', document.getElementById('doc_date').value);
  fd.append('vendor_name', document.getElementById('vendor').value || '');
  fd.append('vendor_tax_id', document.getElementById('vtax').value || '');
  const amt = document.getElementById('amount').value;
  if (amt) fd.append('amount', amt);
  pages.forEach(f => fd.append('images', f, f.name || 'page.jpg'));

  try {
    // relative to the page URL → adapts to any server mount
    const r = await fetch('upload/', { method:'POST', body: fd });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = document.getElementById('doc_date').value;
    document.getElementById('done-sub').textContent =
        (kind === 'income' ? 'הכנסה' : 'הוצאה') + ' • ' + d +
        ' • ' + pages.length + ' עמודים';
    document.getElementById('form-view').style.display = 'none';
    document.getElementById('done-view').style.display = 'block';
  } catch (e) {
    err.textContent = 'השליחה נכשלה — נסה שוב (' + e.message + ')';
    err.style.display = 'block';
    btn.disabled = false; btn.textContent = 'שמור בארכיון ✓';
  }
}

function reset() {
  pages = []; renderPages();
  document.getElementById('vendor').value = '';
  document.getElementById('vtax').value = '';
  document.getElementById('amount').value = '';
  document.getElementById('send').textContent = 'שמור בארכיון ✓';
  refresh();
  document.getElementById('done-view').style.display = 'none';
  document.getElementById('form-view').style.display = 'block';
}
</script>
</body>
</html>
"""


def render_scan_page(token: str) -> str:
    return SCAN_HTML.replace('__TOKEN__', token)