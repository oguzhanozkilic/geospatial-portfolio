import os
import requests
import geopandas as gpd
from sqlalchemy import create_engine, text

# ── Bağlantı (Aynı Kalıyor) ──────────────────────────────────────────────────
# ... (Bağlantı kodların burada kalsın)

# ── 1. FEMA NFHL — Delaware (FIPS: 10) ────────────────────────────────────────
FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"

print("📥 FEMA flood zone verisi çekiliyor...")

# DİKKAT: SORUNU BULMAK İÇİN PARAMETRELERİ MİNİMUMA İNDİRİYORUZ
params = {
    "where": "1=1",           # Tüm verileri getirmeyi dene (sütun filtrelerini devre dışı bırak)
    "outFields": "*",         # Tüm sütunları iste
    "returnGeometry": "false",# GEOMETRİ İSTEMİYORUZ (Sorun dönüşümde mi görelim)
    "f": "json",              # GeoJSON değil, standart JSON iste (daha hafif)
    "resultRecordCount": 10   # SADECE 10 KAYIT (Timeout/limit sorununu ekarte et)
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

print(f"Gönderilen Parametreler: {params}")

response = requests.get(FEMA_URL, params=params, headers=headers, timeout=120)

print(f"Status: {response.status_code}")

try:
    data = response.json()
except requests.exceptions.JSONDecodeError:
    raise ValueError(f"API JSON dönmedi. Yanıt: {response.text[:500]}")

if "error" in data:
    print(f"🚨 FEMA API HATA DETAYI: {data['error']}")
    # Hata devam ederse kodu burada durdur
    exit(1) 

print(f"✅ Başarılı! İlk kayıt örneği: {data.get('features', [])[0]}")

# (Geri kalan kodu geçici olarak yorum satırına al veya silme, sadece buraya kadar test edelim)