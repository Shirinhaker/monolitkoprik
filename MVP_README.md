# Ko‘prik v1656 — bo‘limlari ochilgan to‘liq kod

Bu repozitoriy Ko‘prik monolit loyihasining deploy qilinadigan to‘liq manba
kodidir. Frontend, backend, admin panel, migratsiya va Railway konfiguratsiyasi
birga saqlanadi.

## Faol bo‘limlar

- ro‘yxatdan o‘tish, kirish va Telegram tasdiqlash;
- oddiy, biznes va xodim profillari;
- manzil tanlash, qidiruv va xarita markerlari;
- mahsulot/xizmatlar va hududiy pullik takliflar;
- Plus/Pro obuna, reklama va qo‘lda to‘lov;
- buyurtmalar, xizmat buyurtmalari va ularning ichki suhbati;
- bildirishnomalar, sharhlar va profil shikoyatlari;
- e’lon yaratish va E’lonlar sahifasi;
- istoriya joylash, ko‘rish va arxiv;
- umumiy suhbatlar;
- tizimlashtirish bo‘limlari;
- Taxi chaqirish va Taxi haydovchi kirish tugmalari;
- `admin.koprik.uz` admin paneli va moderatsiya.

## Ataylab o‘zgartirilmagan qismlar

- **AI yordamchi** xavfsizlik sabab faqat privileged biznes profillarida
  ko‘rinadi. Uning ruxsat tekshiruvi olib tashlanmagan.
- **Hisobot** ekrani tayyor funksional modul emas; unda “Keyingi bosqich”
  yozuvi bor. Ishlamaydigan bo‘sh ekran oddiy foydalanuvchiga ochilmagan.
- admin autentifikatsiyasi, akkaunt bloklari, xodim vakolatlari va private
  kvitansiya himoyasi saqlangan.

## Feature flaglar

Tayyor bo‘limlar odatda ochiq ishlaydi. Railway Variables orqali istalganini
alohida vaqtincha yopish mumkin:

```env
MVP_LISTINGS_ENABLED=1
MVP_STORIES_ENABLED=1
MVP_CHAT_ENABLED=1
MVP_SYSTEMIZATION_ENABLED=1
MVP_TAXI_ENABLED=1
```

`1` — ochiq, `0` — yopiq. Frontend `/api/features` javobini yuklagach tegishli
`data-feature` elementlarini ko‘rsatadi yoki yashiradi. Listings, stories, chat
va tizimlashtirish backend API’lari ham server guard bilan himoyalangan.

Taxi va dostavka bir xil `/api/driver` hamda `/api/rides` oqimlaridan
foydalanadi. Shu sabab umumiy backend yo‘llari taxi flagi bilan to‘silmagan:
Taxi kirish nuqtalari frontend flag bilan boshqariladi, dostavka oqimi esa
buzilmaydi.

## Eski bazadagi qulfni yechish

Eski MVP migratsiyasi `platform_feature_flags` jadvaliga
`updated_by_tg_id=0`, `enabled=0` ko‘rinishidagi texnik qulf yozuvlarini qo‘ygan
bo‘lishi mumkin. Joriy kod bunday eski texnik yozuvlarning yangi Railway
sozlamasini bosib ketishiga yo‘l qo‘ymaydi. Haqiqiy admin qo‘ygan override esa
saqlanib qoladi.

`migration_check.py` endi bo‘limlarni qayta yopmaydi. U backup yaratib, faqat
eski texnik qulf yozuvlarini tozalaydi.

## Railway

1. Railway Volume’ni `/data` manziliga ulang.
2. `.env.production.example`dagi nomlarni Railway Variables’ga kiriting.
3. Yuqoridagi beshta `MVP_*_ENABLED` qiymatini `1` qiling.
4. `railpack.json` `unlocked_app:app` entrypointini va `/readyz` healthcheck’ni
   boshqaradi.

`/readyz` feature flaglar faqat yopiq bo‘lishini talab qilmaydi. U barcha
kutilgan flaglar to‘g‘ri boolean holatda o‘qilganini tekshiradi, shuning uchun
bo‘limlarni ochish Railway deploy’ini `Unhealthy` qilmaydi.

Tekshirish manzillari:

```text
/api/features
/api/build
/readyz
```

`/api/features` natijasida quyidagilar `true` bo‘lishi kerak:

```json
{
  "listings": true,
  "stories": true,
  "chat": true,
  "systemization": true,
  "taxi": true
}
```

Sirlar, haqiqiy `.env`, production bazasi va foydalanuvchi uploadlari
repozitoriyga kiritilmaydi. Ular Railway Variables va Volume’da qolishi kerak.
