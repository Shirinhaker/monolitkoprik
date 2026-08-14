"""
Platforma AI Agent — alohida fayl.

v1464:
  - /api/ai/chat              (read-only biznes xulosasi)
  - /api/ai/history           (AI chat tarixi)
  - /api/ai/documents/draft   (Hujjatlar bo'limi uchun AI hujjat drafti)

AI birinchi navbatda draft yaratadi. Hujjatni rasmiylashtirishdan oldin foydalanuvchi
matnni tekshirishi va o'zi saqlashi kerak.
"""

import os
import re
import json
import time
import calendar
import datetime as _dt

import httpx
from fastapi import APIRouter, Request, Header, HTTPException

from database import db
from api import require_business, deny_staff, _row_val
from access_config import is_privileged_tg_id

router = APIRouter(prefix="/api")
TASHKENT_TZ = 5 * 3600


def _require_privileged_ai(conn, user):
    if not is_privileged_tg_id(_row_val(user, "tg_id", None)):
        conn.close()
        raise HTTPException(403, "AI yordamchi ushbu profil uchun yopiq.")


# ====================================================================
# Umumiy yordamchilar
# ====================================================================
def _ensure_ai_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_chat_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_hist_biz ON ai_chat_history(business_id, created_at)")


def _today_iso():
    d = _dt.datetime.fromtimestamp(int(time.time()) + TASHKENT_TZ, _dt.timezone.utc).date()
    return d.isoformat()


def _today_bounds():
    d = _dt.datetime.fromtimestamp(int(time.time()) + TASHKENT_TZ, _dt.timezone.utc).date()
    s = calendar.timegm(d.timetuple()) - TASHKENT_TZ
    return s, s + 86400, d.isoformat()


def _money(v):
    try:
        return f"{int(v or 0):,}".replace(",", " ") + " so'm"
    except Exception:
        return "0 so'm"


def _safe_sum(conn, sql, args=()):
    try:
        r = conn.execute(sql, args).fetchone()
        return int((r[0] if r else 0) or 0)
    except Exception:
        return 0


def _json_dumps(x):
    return json.dumps(x, ensure_ascii=False, indent=2)


def _extract_text_from_openai_response(data):
    # Responses API javobidan matnni robust ajratish
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()
    parts = []
    for item in data.get("output", []) or []:
        for c in item.get("content", []) or []:
            if isinstance(c, dict):
                if isinstance(c.get("text"), str):
                    parts.append(c["text"])
                elif isinstance(c.get("content"), str):
                    parts.append(c["content"])
    return "\n".join(parts).strip()


async def _openai_answer(system_prompt, user_prompt, max_output_tokens=1800):
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return ""
    model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_output_tokens": max_output_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code >= 400:
            return ""
        return _extract_text_from_openai_response(r.json())
    except Exception:
        return ""


# ====================================================================
# Biznes konteksti — read-only
# ====================================================================
def _collect_business_context(conn, biz):
    bid = biz["id"]
    ts, te, today = _today_bounds()
    revenue_today = _safe_sum(
        conn,
        "SELECT COALESCE(SUM(total),0) FROM sales WHERE business_id=? AND created_at>=? AND created_at<? AND COALESCE(source,'')<>'qarzpay'",
        (bid, ts, te),
    )
    expenses_today = _safe_sum(
        conn,
        "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE business_id=? AND created_at>=? AND created_at<?",
        (bid, ts, te),
    )
    debt_total = 0
    try:
        r = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN q.type='debt' THEN q.amount ELSE -q.amount END),0) bal
               FROM qarz_tx q JOIN debtors d ON d.id=q.debtor_id
               WHERE d.business_id=?""",
            (bid,),
        ).fetchone()
        debt_total = int((r["bal"] if r else 0) or 0)
    except Exception:
        pass
    low_stock = []
    try:
        rows = conn.execute(
            """SELECT name, stock_qty, unit, min_qty FROM items
               WHERE business_id=? AND COALESCE(track_stock,0)=1
                 AND (COALESCE(stock_qty,0)<=COALESCE(min_qty,0) OR COALESCE(stock_qty,0)<=5)
               ORDER BY COALESCE(stock_qty,0) ASC, name ASC LIMIT 8""",
            (bid,),
        ).fetchall()
        low_stock = [{"name": r["name"], "qty": r["stock_qty"], "unit": r["unit"] or "dona"} for r in rows]
    except Exception:
        pass
    orders = {}
    try:
        rows = conn.execute(
            """SELECT status, COUNT(*) c FROM orders
               WHERE provider_kind='business' AND provider_actor_id=? GROUP BY status""",
            (bid,),
        ).fetchall()
        orders = {r["status"] or "unknown": int(r["c"] or 0) for r in rows}
    except Exception:
        pass
    return {
        "business": {
            "id": bid,
            "name": biz["name"],
            "yon": _row_val(biz, "yon", "") or "",
            "tur": _row_val(biz, "tur", "") or "",
            "director": _row_val(biz, "director", "") or "",
            "inn": _row_val(biz, "inn", "") or "",
            "address": _row_val(biz, "address", "") or "",
            "phone": _row_val(biz, "phone", "") or "",
        },
        "today": today,
        "today_summary": {"revenue": revenue_today, "expenses": expenses_today, "profit": revenue_today - expenses_today},
        "debt_total": debt_total,
        "low_stock": low_stock,
        "orders_by_status": orders,
    }


def _local_chat_answer(message, ctx):
    msg = (message or "").lower()
    s = ctx.get("today_summary", {})
    if any(w in msg for w in ["savdo", "tushum", "foyda", "statistika"]):
        return (
            "📊 Bugungi xulosa:\n"
            "• Tushum: " + _money(s.get("revenue")) + "\n"
            "• Xarajat: " + _money(s.get("expenses")) + "\n"
            "• Sof foyda: " + _money(s.get("profit"))
        )
    if any(w in msg for w in ["ombor", "qoldiq", "kam"]):
        low = ctx.get("low_stock") or []
        if not low:
            return "📦 Hozir kam qolgan tovar topilmadi."
        rows = ["📦 Kam qolgan tovarlar:"]
        for x in low[:6]:
            rows.append("• " + str(x.get("name") or "Nomsiz") + " — " + str(x.get("qty") or 0) + " " + str(x.get("unit") or "dona"))
        return "\n".join(rows)
    if any(w in msg for w in ["qarz", "debitor"]):
        return "📒 Umumiy qarz qoldig'i: " + _money(ctx.get("debt_total")) + "."
    if any(w in msg for w in ["buyurtma", "zakaz"]):
        orders = ctx.get("orders_by_status") or {}
        if not orders:
            return "📥 Hozir buyurtmalar statistikasi topilmadi."
        return "📥 Buyurtmalar holati:\n" + "\n".join("• " + k + ": " + str(v) for k, v in orders.items())
    return (
        "🤖 Bugungi qisqa xulosa:\n"
        "• Tushum: " + _money(s.get("revenue")) + "\n"
        "• Xarajat: " + _money(s.get("expenses")) + "\n"
        "• Sof foyda: " + _money(s.get("profit")) + "\n"
        "• Qarz qoldig'i: " + _money(ctx.get("debt_total")) + "\n"
        "• Kam qolgan tovarlar: " + str(len(ctx.get("low_stock") or [])) + " ta\n\n"
        "Ombor, qarz, buyurtma yoki savdo bo'yicha aniqroq so'rasangiz, batafsil aytaman."
    )


@router.api_route("/ai/chat", methods=["GET", "POST"])
@router.api_route("/ai/chat/", methods=["GET", "POST"], include_in_schema=False)
async def ai_chat(request: Request, x_telegram_init_data: str = Header(default="")):
    """
    AI chat. Asosiy usul POST. GET ham eski frontend/proxylarda 405 chiqmasligi
    uchun moslik rejimi sifatida qabul qilinadi.
    """
    conn = db()
    user, biz = require_business(conn, x_telegram_init_data)
    _require_privileged_ai(conn, user)
    deny_staff(conn, x_telegram_init_data, "AI yordamchi")
    _ensure_ai_tables(conn)
    if request.method == "GET":
        b = {"message": request.query_params.get("message", "")}
    else:
        try:
            b = await request.json()
        except Exception:
            b = {}
    msg = (b.get("message") or "").strip()
    if not msg:
        conn.close()
        raise HTTPException(400, "Savol yozing.")
    ctx = _collect_business_context(conn, biz)
    system = "Sen Platforma biznes kabinetidagi AI yordamchisan. O'zbek tilida, sodda va aniq javob ber. Faqat berilgan biznes kontekstiga asoslan."
    user_prompt = "Biznes konteksti:\n" + _json_dumps(ctx) + "\n\nSavol:\n" + msg
    answer = await _openai_answer(system, user_prompt, 1200)
    if not answer:
        answer = _local_chat_answer(msg, ctx)
    now = int(time.time())
    conn.execute("INSERT INTO ai_chat_history(business_id,user_id,role,text,created_at) VALUES(?,?,?,?,?)", (biz["id"], user["id"], "user", msg, now))
    conn.execute("INSERT INTO ai_chat_history(business_id,user_id,role,text,created_at) VALUES(?,?,?,?,?)", (biz["id"], user["id"], "assistant", answer, now))
    conn.commit()
    conn.close()
    return {"ok": True, "answer": answer}


@router.get("/ai/history")
@router.get("/ai/history/", include_in_schema=False)
async def ai_history(limit: int = 40, x_telegram_init_data: str = Header(default="")):
    limit = max(1, min(int(limit or 40), 100))
    conn = db()
    user, biz = require_business(conn, x_telegram_init_data)
    _require_privileged_ai(conn, user)
    deny_staff(conn, x_telegram_init_data, "AI yordamchi")
    _ensure_ai_tables(conn)
    rows = conn.execute(
        "SELECT role, text, created_at FROM ai_chat_history WHERE business_id=? ORDER BY id DESC LIMIT ?",
        (biz["id"], limit),
    ).fetchall()
    conn.close()
    return {"history": [{"role": r["role"], "text": r["text"], "created_at": r["created_at"]} for r in rows][::-1]}


@router.get("/ai/status")
async def ai_status(x_telegram_init_data: str = Header(default="")):
    """Frontend va backend bir xil build ekanini tekshirish uchun."""
    conn = db()
    user, biz = require_business(conn, x_telegram_init_data)
    _require_privileged_ai(conn, user)
    deny_staff(conn, x_telegram_init_data, "AI yordamchi")
    _ensure_ai_tables(conn)
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "build": "v1469",
        "business_id": biz["id"],
        "openai_enabled": bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
        "local_fallback": True,
    }


# ====================================================================
# AI HUJJAT GENERATORI
# ====================================================================
def _norm(s):
    return (s or "").strip()


def _pick_doc_type(prompt, direction="", doc_type=""):
    p = (prompt or "").lower()
    if doc_type and doc_type not in ("Erkin shakldagi hujjat", ""):
        # Foydalanuvchi o'zi aniq tanlagan bo'lsa, shuni hurmat qilamiz
        return direction or "ichki", doc_type
    if "shartnoma" in p or "xizmat ko'rsatish" in p or "oldi-sotdi" in p:
        return "chiquvchi", "Shartnoma"
    if "hisob" in p or "invoice" in p or "faktura" in p:
        return "chiquvchi", "Hisob-faktura"
    if "yuk" in p or "tovar topshir" in p or "yetkaz" in p:
        return "chiquvchi", "Yuk xati"
    if "ishonchnoma" in p:
        return "chiquvchi", "Ishonchnoma"
    if "solishtirma" in p:
        return "chiquvchi", "Solishtirma dalolatnoma"
    if "akt" in p or "bajarilgan" in p:
        return "chiquvchi", "Akt"
    if "dalolatnoma" in p:
        return direction or "ichki", "Dalolatnoma"
    if "buyruq" in p:
        return "ichki", "Buyruq"
    if "ariza" in p:
        return "ichki", "Ariza"
    if "bayonnoma" in p:
        return direction or "ichki", "Bayonnoma"
    if "tilxat" in p or "qarz" in p:
        return "ichki", "Tilxat"
    if "xabarnoma" in p or "ogohlantirish" in p:
        return "ichki", "Xabarnoma"
    return direction or "ichki", doc_type or "Erkin shakldagi hujjat"


def _amount_from_prompt(prompt):
    p = prompt or ""
    m = re.search(r"(\d[\d\s.,]{2,})\s*(?:so['’`]?m|sum|uzs|сум)?", p, flags=re.I)
    if not m:
        return "[summa]"
    raw = re.sub(r"[^0-9]", "", m.group(1))
    return _money(raw) if raw else m.group(1).strip()


def _contractor_from_payload(conn, biz_id, contractor_id):
    if not contractor_id:
        return {}
    try:
        r = conn.execute("SELECT * FROM contractors WHERE id=? AND business_id=?", (int(contractor_id), biz_id)).fetchone()
        if not r:
            return {}
        return {
            "id": r["id"], "name": r["name"] or "", "director": r["director"] or "",
            "phone": r["phone"] or "", "address": r["address"] or "", "inn": r["inn"] or "",
            "account": r["account"] or "", "bank": r["bank"] or "", "mfo": r["mfo"] or "",
        }
    except Exception:
        return {}


def _business_doc_ctx(biz, body, contractor):
    return {
        "firma": _norm(body.get("firm_name")) or _row_val(biz, "name", "") or "[Firma nomi]",
        "rahbar": _norm(body.get("director")) or _row_val(biz, "director", "") or "[Rahbar F.I.Sh.]",
        "inn": _norm(body.get("inn")) or _row_val(biz, "inn", "") or "[STIR]",
        "address": _row_val(biz, "address", "") or "[Manzil]",
        "phone": _row_val(biz, "phone", "") or "[Telefon]",
        "sana": _norm(body.get("doc_date")) or _today_iso(),
        "raqam": _norm(body.get("number")) or "___",
        "title": _norm(body.get("title")),
        "contr": contractor.get("name") or "[Kontragent / mijoz nomi]",
        "contr_dir": contractor.get("director") or "[Kontragent rahbari]",
        "contr_inn": contractor.get("inn") or "[STIR]",
        "contr_acc": contractor.get("account") or "[hisob raqam]",
        "contr_bank": contractor.get("bank") or "[bank]",
        "contr_mfo": contractor.get("mfo") or "[MFO]",
        "contr_addr": contractor.get("address") or "[manzil]",
    }


def _local_doc_body(prompt, direction, doc_type, c):
    amount = _amount_from_prompt(prompt)
    title = c.get("title") or prompt[:80].strip() or doc_type
    if doc_type == "Shartnoma":
        return f"""{c['firma']}

XIZMAT KO'RSATISH SHARTNOMASI
№ {c['raqam']}                                      {c['sana']}

1. TOMONLAR
Ijrochi: {c['firma']}, rahbari {c['rahbar']}.
Buyurtmachi: {c['contr']}, rahbari {c['contr_dir']}.

2. SHARTNOMA PREDMETI
Ijrochi Buyurtmachiga quyidagi xizmat/tovar bo'yicha ish bajaradi:
{prompt}

3. SHARTNOMA SUMMASI
Umumiy summa: {amount}.
To'lov tartibi: tomonlar kelishuviga asosan naqd, karta yoki bank orqali amalga oshiriladi.

4. BAJARISH MUDDATI
Xizmat/tovar topshirish muddati: [muddatni kiriting].

5. TOMONLARNING MAJBURIYATLARI
Ijrochi ishni sifatli bajarish va o'z vaqtida topshirish majburiyatini oladi.
Buyurtmachi bajarilgan ishni qabul qilish va to'lovni amalga oshirish majburiyatini oladi.

6. NIZOLARNI HAL QILISH
Nizolar muzokara yo'li bilan, kelishilmasa amaldagi qonunchilik tartibida hal qilinadi.

7. REKVIZITLAR
Ijrochi: {c['firma']}
STIR: {c['inn']}
Manzil: {c['address']}
Telefon: {c['phone']}
Rahbar: {c['rahbar']}

Buyurtmachi: {c['contr']}
STIR: {c['contr_inn']}
Manzil: {c['contr_addr']}
Bank: {c['contr_bank']} MFO: {c['contr_mfo']}
Hisob raqami: {c['contr_acc']}

Imzolar:
Ijrochi: ____________________     Buyurtmachi: ____________________
M.O'.                              M.O'.
"""
    if doc_type in ("Akt", "Dalolatnoma"):
        return f"""{c['firma']}

BAJARILGAN ISHLAR DALOLATNOMASI
№ {c['raqam']}                                      {c['sana']}

Biz, quyida imzo chekuvchilar:
Ijrochi: {c['firma']} nomidan {c['rahbar']},
Buyurtmachi: {c['contr']} nomidan {c['contr_dir']},

quyidagi ish/xizmatlar bajarilganligini tasdiqlaymiz:
{prompt}

Umumiy summa: {amount}.

Buyurtmachi bajarilgan ish/xizmatlarni qabul qildi va e'tirozi yo'q.
Mazkur dalolatnoma ikki nusxada tuzildi.

Ijrochi: ____________________  {c['rahbar']}
Buyurtmachi: _________________  {c['contr_dir']}
"""
    if doc_type == "Hisob-faktura":
        return f"""{c['firma']}

HISOB / INVOICE
№ {c['raqam']}                                      {c['sana']}

Yetkazib beruvchi: {c['firma']}
STIR: {c['inn']}
Manzil: {c['address']}
Telefon: {c['phone']}

Xaridor: {c['contr']}
STIR: {c['contr_inn']}

Tovar/xizmat nomi:
{prompt}

To'lov summasi: {amount}
To'lov muddati: [muddat]
To'lov usuli: [naqd/karta/bank]

Rahbar: ____________________ {c['rahbar']}
M.O'.
"""
    if doc_type == "Yuk xati":
        return f"""{c['firma']}

YUK XATI
№ {c['raqam']}                                      {c['sana']}

Jo'natuvchi: {c['firma']}
Qabul qiluvchi: {c['contr']}

Tovarlar / yuk tavsifi:
{prompt}

Miqdor: [miqdor]
Summa: {amount}
Yetkazish manzili: {c['contr_addr']}

Topshirdi: ____________________ {c['rahbar']}
Qabul qildi: __________________ {c['contr_dir']}
"""
    if doc_type == "Buyruq":
        return f"""{c['firma']}

BUYRUQ
№ {c['raqam']}                                      {c['sana']}

{title.upper()}

BUYURAMAN:
1. {prompt}
2. Mas'ul shaxs: [F.I.Sh.]
3. Ijro muddati: [muddat]

Asos: [asos hujjat]

Rahbar: ____________________ {c['rahbar']}
M.O'.
"""
    if doc_type == "Tilxat":
        return f"""TILXAT

Sana: {c['sana']}

Men, [F.I.Sh.], {c['firma']} / {c['rahbar']}dan quyidagilarni oldim yoki majburiyat oldim:
{prompt}

Summa: {amount}
Qaytarish muddati: [muddat]

Tilxat beruvchi: ____________________ [F.I.Sh.]
Pasport/JShShIR: [ma'lumot]
Telefon: [telefon]

Guvoh: ____________________ [F.I.Sh.]
"""
    if doc_type == "Xabarnoma":
        return f"""{c['firma']}

XABARNOMA
№ {c['raqam']}                                      {c['sana']}

Kimga: {c['contr']}

Hurmatli hamkor/mijoz,

Sizga quyidagi masala bo'yicha xabar beramiz:
{prompt}

Iltimos, ushbu xabarnomani ko'rib chiqib, belgilangan muddatda javob berishingizni so'raymiz.

Hurmat bilan,
{c['firma']}
Rahbar: ____________________ {c['rahbar']}
"""
    return f"""{c['firma']}

{doc_type.upper()}
№ {c['raqam']}                                      {c['sana']}

Sarlavha: {title}

Hujjat mazmuni:
{prompt}

Qo'shimcha ma'lumotlar:
- Firma: {c['firma']}
- Rahbar: {c['rahbar']}
- Kontragent: {c['contr']}
- Summa: {amount}

Imzo: ____________________ {c['rahbar']}
M.O'.
"""


@router.post("/ai/documents/draft")
async def ai_document_draft(request: Request, x_telegram_init_data: str = Header(default="")):
    conn = db()
    user, biz = require_business(conn, x_telegram_init_data)
    _require_privileged_ai(conn, user)
    deny_staff(conn, x_telegram_init_data, "AI hujjat yaratish")
    body = await request.json()
    prompt = _norm(body.get("prompt"))
    if not prompt:
        conn.close()
        raise HTTPException(400, "AI uchun hujjat topshirig'ini yozing.")
    direction, doc_type = _pick_doc_type(prompt, _norm(body.get("direction")), _norm(body.get("doc_type")))
    contractor = _contractor_from_payload(conn, biz["id"], body.get("contractor_id"))
    c = _business_doc_ctx(biz, body, contractor)
    ctx = {
        "business": {"name": c["firma"], "director": c["rahbar"], "inn": c["inn"], "address": c["address"], "phone": c["phone"]},
        "contractor": contractor,
        "requested_direction": direction,
        "requested_doc_type": doc_type,
        "date": c["sana"],
        "number": c["raqam"],
        "user_prompt": prompt,
    }
    system = (
        "Sen Platforma ilovasidagi AI hujjat generatorisan. "
        "Faqat o'zbek tilida rasmiy, sodda, tahrirlashga tayyor DRAFT hujjat matni yoz. "
        "Hujjatni yakuniy huquqiy maslahat deb ko'rsatma; foydalanuvchi tekshirishi kerak. "
        "Rekvizitlar yetishmasa [..] ko'rinishida joy qoldir. Faqat hujjat matnini qaytar."
    )
    user_prompt = "Kontekst:\n" + _json_dumps(ctx) + "\n\nShu topshiriq bo'yicha hujjat draftini yoz."
    ai_body = await _openai_answer(system, user_prompt, 2200)
    source = "openai" if ai_body else "local"
    if not ai_body:
        ai_body = _local_doc_body(prompt, direction, doc_type, c)
    title = _norm(body.get("title")) or (prompt[:70] + ("..." if len(prompt) > 70 else ""))
    conn.close()
    return {
        "ok": True,
        "source": source,
        "direction": direction,
        "doc_type": doc_type,
        "title": title,
        "number": _norm(body.get("number")) or "",
        "doc_date": _norm(body.get("doc_date")) or _today_iso(),
        "body": ai_body.strip(),
        "note": "Bu AI draft. Saqlashdan oldin tekshiring.",
    }
