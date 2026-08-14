# Ko‘prik MVP v1656 — to‘liq kod

Bu papka Ko‘prik loyihasining birinchi **onlaynlashtirish MVP** bosqichi
uchun to‘liq, deploy qilinadigan manba kodidir. Arxiv ichida frontend,
backend, admin panel, migratsiya, deploy konfiguratsiyasi, testlar va texnik
hujjatlar birga beriladi.

## MVPda faol

- ro‘yxatdan o‘tish, kirish va Telegram tasdiqlash;
- oddiy, biznes va xodim profillari;
- manzil tanlash, qidiruv va xarita markerlari;
- mahsulot/xizmatlar va hududiy pullik takliflar;
- Plus/Pro obuna, reklama va qo‘lda to‘lov;
- buyurtmalar, xizmat buyurtmalari va ularning ichki suhbati;
- bildirishnomalar, sharhlar va profil shikoyatlari;
- `admin.koprik.uz` admin paneli va moderatsiya.

## MVPda yopiq

- e’lon yaratish va E’lonlar sahifasi;
- istoriya joylash va istoriya ko‘rish;
- umumiy suhbatlar;
- tizimlashtirish bo‘limlari;
- Taxi chaqirish va Taxi haydovchi kirish tugmalari.

Yopiq bo‘limlarning mavjud ma’lumotlari o‘chirilmaydi. Keyin ochish uchun
ularning kodlari feature guard ortida saqlangan. Taxi tugmalari ham
foydalanuvchiga ko‘rsatilmaydi va to‘g‘ridan-to‘g‘ri ekran ochilishi
frontendda bloklangan. Yetkazib berish/buyurtma backend oqimi saqlangan.

## v1656 o‘zgarishlari

- bosh sahifa Leaflet xaritasi `zoomControl:false` bilan ochiladi; shu sabab
  telefon va kompyuterda `+ / −` tugmalari yaratilmaydi;
- `taxiBtn` va `taxiCabBtn` `data-feature="taxi"` orqali yopilgan;
- build javobida `taxi_call_enabled=false`;
- to‘liq MVP manba paketi uchun ushbu hujjat qo‘shildi.

## Railway

1. Kodni GitHub repozitoriyga yuklang.
2. Railway Volume’ni `/data` manziliga ulang.
3. `.env.production.example`dagi nomlarni Railway Variables’ga kiriting.
4. `railpack.json` loyihaning start va `/readyz` healthcheck sozlamalarini
   beradi.

Sirlar, haqiqiy `.env`, production bazasi va foydalanuvchi uploadlari ZIPga
kiritilmagan. Ular Railway Variables va Volume’da qolishi kerak.

## Lokal tekshiruv

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

