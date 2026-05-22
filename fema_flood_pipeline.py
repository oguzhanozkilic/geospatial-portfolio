import os
import io
import json
import tempfile
import requests
import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text

# ── Bağlantı ──────────────────────────────────────────────────────────────────
DB_HOST     = os.environ["DB_HOST"]
DB_PORT     = os.environ["DB_PORT"]
DB_NAME     = os.environ["DB_NAME"]
DB_USER     = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

connection_string = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(connection_string)

# ── 1. FEMA NFHL — Delaware (FIPS: 10) ────────────────────────────────────────
FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"

print("📥 FEMA flood zone verisi çekiliyor...")

params = {
    "where": "STATE_ABBR='DE'",
    "outFields": "FLD_ZONE,SFHA_TF,STUDY_TYP",
    "returnGeometry": "true",
    "f": "geojson",
    "outSR": "4326",
    "resultRecordCount": 5000,
}

response = requests.get(FEMA_URL, params=params, timeout=120)
response.raise_for_status()

print(f"Status: {response.status_code}")
print(f"Preview: {response.text[:200]}")

# GeoJSON parse — engine bağımlılığı yok
geojson = json.loads(response.text)

if "error" in geojson:
    raise ValueError(f"FEMA API hatası: {geojson['error']}")

features = geojson.get("features", [])
if len(features) == 0:
    raise ValueError("FEMA API boş response döndürdü")

print(f"✅ {len(features)} feature çekildi")

fema_gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

sfha = fema_gdf[fema_gdf["SFHA_TF"] == "T"].copy()
print(f"✅ {len(sfha)} SFHA (yüksek risk) feature")

# ── 2. Supabase'e yükle ───────────────────────────────────────────────────────
print("💾 FEMA verisi Supabase'e yükleniyor...")

fema_gdf.to_postgis(
    name="fema_flood_zones_delaware",
    con=engine,
    if_exists="replace",
    index=False,
    chunksize=500
)
print(f"✅ fema_flood_zones_delaware yüklendi ({len(fema_gdf)} satır)")

# ── 3. Tract-SFHA spatial join ────────────────────────────────────────────────
print("🔄 Tract-SFHA spatial join çalışıyor...")

flood_join_query = text("""
    ALTER TABLE census_tracts_delaware
        ADD COLUMN IF NOT EXISTS flood_risk_pct NUMERIC,
        ADD COLUMN IF NOT EXISTS is_sfha BOOLEAN;

    UPDATE census_tracts_delaware ct
    SET
        flood_risk_pct = sub.overlap_pct,
        is_sfha        = (sub.overlap_pct > 10)
    FROM (
        SELECT
            ct."GEOID",
            ROUND(
                100.0 * SUM(ST_Area(ST_Intersection(ct.geometry, fz.geometry)))
                / NULLIF(ST_Area(ct.geometry), 0),
                2
            ) AS overlap_pct
        FROM census_tracts_delaware ct
        JOIN fema_flood_zones_delaware fz
            ON ST_Intersects(ct.geometry, fz.geometry)
        WHERE fz."SFHA_TF" = 'T'
        GROUP BY ct."GEOID"
    ) sub
    WHERE ct."GEOID" = sub."GEOID";
""")

with engine.connect() as conn:
    conn.execute(flood_join_query)
    conn.commit()
    result = conn.execute(text("""
        SELECT "GEOID", flood_risk_pct, is_sfha
        FROM census_tracts_delaware
        WHERE flood_risk_pct IS NOT NULL
        ORDER BY flood_risk_pct DESC
        LIMIT 5;
    """))
    print("\n🌊 En yüksek flood riskli tract'lar:")
    for row in result:
        print(row)

# ── 4. Investment score v2 ────────────────────────────────────────────────────
print("\n🔄 Investment score v2 hesaplanıyor...")

score_query = text("""
    ALTER TABLE census_tracts_delaware
        ADD COLUMN IF NOT EXISTS investment_score_v2 NUMERIC;

    UPDATE census_tracts_delaware
    SET investment_score_v2 = (
        CASE WHEN median_income >= 89400 THEN 3
             WHEN median_income >= 71520 THEN 2
             WHEN median_income >= 53640 THEN 1
             ELSE 0 END
        +
        CASE WHEN pct_vacant < 5  THEN 3
             WHEN pct_vacant < 10 THEN 2
             WHEN pct_vacant < 15 THEN 1
             ELSE 0 END
        +
        CASE WHEN median_home_value >= 300000 THEN 3
             WHEN median_home_value >= 200000 THEN 2
             WHEN median_home_value >= 100000 THEN 1
             ELSE 0 END
        +
        CASE WHEN flood_risk_pct IS NULL THEN 0
             WHEN flood_risk_pct >= 50   THEN -3
             WHEN flood_risk_pct >= 25   THEN -2
             WHEN flood_risk_pct >= 10   THEN -1
             ELSE 0 END
    )
    WHERE median_income IS NOT NULL;
""")

with engine.connect() as conn:
    conn.execute(score_query)
    conn.commit()
    result = conn.execute(text("""
        SELECT "GEOID", investment_score, investment_score_v2, flood_risk_pct,
               (investment_score_v2 - investment_score) AS delta
        FROM census_tracts_delaware
        WHERE flood_risk_pct > 10 AND investment_score IS NOT NULL
        ORDER BY delta ASC
        LIMIT 5;
    """))
    print("\n📉 Flood nedeniyle en çok düşen tract'lar:")
    for row in result:
        print(row)

# ── 5. GeoJSON export ─────────────────────────────────────────────────────────
print("\n📤 GeoJSON export ediliyor...")

export_query = """
    SELECT "GEOID", median_income, median_home_value, pct_vacant, population,
           flood_risk_pct, is_sfha,
           investment_score    AS score_v1,
           investment_score_v2 AS score_v2,
           geometry
    FROM census_tracts_delaware
    WHERE investment_score_v2 IS NOT NULL
"""

gdf_final = gpd.read_postgis(export_query, engine, geom_col="geometry")
gdf_final.to_file("delaware_investment_scores_v2.geojson", driver="GeoJSON")
print(f"✅ {len(gdf_final)} tract export edildi")
print("\nTamamlandı.")