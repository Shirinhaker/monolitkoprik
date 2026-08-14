"""Backend-owned Uzbekistan region/district catalog."""

REGION_DISTRICTS = {
    "Toshkent shahri": (
        "Bektemir", "Chilonzor", "Mirobod", "Mirzo Ulug'bek",
        "Olmazor", "Sergeli", "Uchtepa", "Shayxontohur",
        "Yakkasaroy", "Yashnobod", "Yunusobod",
    ),
    "Toshkent viloyati": (
        "Angren", "Bekobod", "Bo'ka", "Bo'stonliq (Gazalkent)",
        "Chinoz", "Chirchiq", "Nurafshon", "Ohangaron",
        "Oqqo'rg'on", "Parkent", "Piskent", "Qibray",
        "Yangiyo'l", "Zangiota",
    ),
    "Andijon viloyati": (
        "Andijon shahri", "Asaka", "Baliqchi", "Bo'z",
        "Buloqboshi", "Izboskan", "Jalaquduq", "Marhamat",
        "Oltinko'l", "Paxtaobod", "Qo'rg'ontepa", "Shahrixon",
        "Ulug'nor", "Xo'jaobod",
    ),
    "Farg'ona viloyati": (
        "Farg'ona shahri", "Marg'ilon", "Qo'qon", "Quvasoy",
        "Beshariq", "Bog'dod", "Buvayda", "Dang'ara", "Furqat",
        "Qo'shtepa", "Rishton", "So'x", "Toshloq", "Uchko'prik",
        "Yozyovon",
    ),
    "Namangan viloyati": (
        "Namangan shahri", "Chortoq", "Chust", "Kosonsoy",
        "Mingbuloq", "Norin", "Pop", "To'raqo'rg'on",
        "Uchqo'rg'on", "Uychi", "Yangiqo'rg'on",
    ),
    "Samarqand viloyati": (
        "Samarqand shahri", "Kattaqo'rg'on", "Bulung'ur",
        "Ishtixon", "Jomboy", "Qo'shrabot", "Narpay (Oqtosh)",
        "Nurobod", "Oqdaryo", "Pastdarg'om", "Paxtachi",
        "Payariq", "Toyloq", "Urgut",
    ),
    "Buxoro viloyati": (
        "Buxoro shahri", "Kogon", "G'ijduvon", "Jondor",
        "Qorako'l", "Qorovulbozor", "Olot", "Peshku", "Romitan",
        "Shofirkon", "Vobkent",
    ),
    "Qashqadaryo viloyati": (
        "Qarshi", "Shahrisabz", "Kitob", "G'uzor", "Qamashi",
        "Koson", "Mirishkor (Pomuq)", "Muborak", "Nishon",
        "Chiroqchi", "Yakkabog'", "Dehqonobod", "Kasbi",
    ),
    "Surxondaryo viloyati": (
        "Termiz", "Denov", "Boysun", "Sho'rchi", "Angor",
        "Jarqo'rg'on", "Qiziriq", "Qumqo'rg'on", "Muzrabot",
        "Oltinsoy", "Sariosiyo", "Sherobod", "Uzun",
    ),
    "Jizzax viloyati": (
        "Jizzax shahri", "Arnasoy", "Baxmal", "Do'stlik",
        "Forish", "G'allaorol", "Mirzacho'l", "Paxtakor",
        "Yangiobod", "Zomin", "Zarbdor", "Sharof Rashidov",
    ),
    "Sirdaryo viloyati": (
        "Guliston", "Yangiyer", "Shirin", "Boyovut",
        "Sayxunobod", "Sardoba", "Mirzaobod", "Oqoltin",
        "Xovos", "Sirdaryo",
    ),
    "Navoiy viloyati": (
        "Navoiy shahri", "Zarafshon", "Karmana", "Konimex",
        "Qiziltepa", "Navbahor", "Nurota", "Tomdi", "Uchquduq",
        "Xatirchi",
    ),
    "Xorazm viloyati": (
        "Urganch", "Xiva", "Bog'ot", "Gurlan", "Xonqa",
        "Hazorasp", "Qo'shko'pir", "Shovot", "Yangiariq",
        "Yangibozor",
    ),
    "Qoraqalpog'iston Respublikasi": (
        "Nukus", "Beruniy", "Chimboy", "Ellikqal'a (Bo'ston)",
        "Kegeyli", "Mo'ynoq", "Qonliko'l", "Qo'ng'irot",
        "Qorao'zak", "Shumanay", "Taxtako'pir", "To'rtko'l",
        "Xo'jayli", "Amudaryo (Mang'it)",
    ),
}

DISTRICT_NAMES = tuple(
    district
    for districts in REGION_DISTRICTS.values()
    for district in districts
)
