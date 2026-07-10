# ==============================================================================
# PHASE 1: STANDARD LIBRARY IMPORTS
# (These are safe to load immediately)
# ==============================================================================
import os
import sys
import glob
import ctypes
import argparse
import datetime
import time
import re

# ==============================================================================
# PHASE 2: ENVIRONMENT SANITIZATION
# (MUST run before any geospatial libraries are imported)
# ==============================================================================
# 1. Remove ArcGIS GDAL settings
os.environ.pop("GDAL_DRIVER_PATH", None)
os.environ.pop("GDAL_SKIP", None)

# 2. Remove ArcGIS from Python path
sys.path = [p for p in sys.path if "arcgis" not in p.lower()]

# 3. Remove ArcGIS / ESRI entries from Windows PATH
os.environ["PATH"] = os.pathsep.join(
    p for p in os.environ.get("PATH", "").split(os.pathsep)
    if "arcgis" not in p.lower() and "esri" not in p.lower()
)

# 4. Silence GDAL logging
os.environ["CPL_LOG"] = "NUL"

# 5. Point GDAL and PROJ to Q-drive env
conda_base = r"Q:\tiles\60_QA\conda\envs\nxgm-py3"
os.environ["GDAL_DATA"] = os.path.join(conda_base, r"Library\share\gdal")
os.environ["PROJ_DATA"] = os.path.join(conda_base, r"Library\share\proj")
os.environ["PROJ_LIB"]  = os.path.join(conda_base, r"Library\share\proj")

# 6. Add Q-drive DLLs to search path first
conda_bin = os.path.join(conda_base, r"Library\bin")
if hasattr(os, "add_dll_directory") and os.path.exists(conda_bin):
    os.add_dll_directory(conda_bin)
os.environ["PATH"] = conda_bin + os.pathsep + os.environ["PATH"]

# 7. Force load GDAL DLLs into memory
for dll in glob.glob(os.path.join(conda_bin, "gdal*.dll")):
    ctypes.CDLL(dll)

# ==============================================================================
# PHASE 3: GEOSPATIAL & THIRD-PARTY IMPORTS
# (Safe to load now that the environment is locked to the Q: drive)
# ==============================================================================
# 3A. Low-level bindings (Configure these immediately upon import)
from osgeo import gdal
gdal.SetConfigOption("GDAL_DRIVER_PATH", "")

import pyproj
pyproj.datadir.set_data_dir(os.environ["PROJ_DATA"])

# 3B. High-level data libraries
import pandas as pd
import geopandas as gpd
from shapely import wkb

# 3C. Database and Custom modules
from sqlalchemy import create_engine
from QA_dashboard import build_dashboard

# ==============================================================================
# SCRIPT EXECUTION STARTS HERE
# ==============================================================================

def ensure_extension(filename, ext):
    if not filename.lower().endswith(ext):
        return filename + ext
    return filename

start_time = time.time()
now = datetime.datetime.now()
date = now.strftime("%d_%m_%Y")
parser = argparse.ArgumentParser()

parser.add_argument("--path",help="Output directory")
parser.add_argument("--stats", help="Output statistics CSV")
parser.add_argument("--geoms", help="Output geometrie issues GEOJSON")
parser.add_argument("--dashboard", help="Output dashboard HTML")

args = parser.parse_args()
if not args.stats:
    args.stats = f"stats_{date}.csv"
    args.stats = ensure_extension(args.stats, ".csv")

if not args.geoms:
    args.geoms = f"geom_issues_{date}.geojson"
    args.geoms = ensure_extension(args.geoms, ".geojson")

if not args.dashboard:
    args.dashboard = f"qa_report_{date}.html"
args.dashboard = ensure_extension(args.dashboard, ".html")

output_dir = args.path
os.makedirs(output_dir, exist_ok=True)
stats_path = os.path.join(output_dir, args.stats)
geom_path = os.path.join(output_dir, args.geoms)
dashboard_path = os.path.join(output_dir, args.dashboard)

def parse_queries(sql_text):
    blocks = re.split(r'(?=-- name:)', sql_text.strip())
    queries = []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        name, query_type, requires, sql_lines = None, "stat", [], []

        for line in lines:
            if line.startswith("-- name:"):
                name = line.replace("-- name:", "").strip()
            elif line.startswith("-- type:"):
                query_type = line.replace("-- type:", "").strip()
            elif line.startswith("-- requires:"):
                requires = [x.strip() for x in line.replace("-- requires:", "").split(",") if x.strip()]
            elif not line.startswith("--"):
                sql_lines.append(line)
        if name:
            queries.append({
                "name": name,
                "requires": requires,
                "sql": "\n".join(sql_lines).strip(),
                "type": query_type
            })
    return queries


def make_engine(params):
    return create_engine(
        f"postgresql+psycopg2://{params['user']}:{params['password']}"
        f"@{params['host']}:{params['port']}/{params['dbname']}",
        connect_args={"connect_timeout": 5}
    )


def decode_geom(x):
    if isinstance(x, str):
        return wkb.loads(bytes.fromhex(x))
    return wkb.loads(bytes(x)) if x is not None else None


DATABASES = {
    "ltvt_master": {
        "host": "10.220.44.61",
        "port": 5433,
        "dbname": "ltvt_master",
        "user": "vt_reader",
        "password": "swisstopo_reader",
    },
    "ltvt_prod": {
        "host": "10.220.44.61",
        "port": 5433,
        "dbname": "ltvt_prod",
        "user": "vt_reader",
        "password": "swisstopo_reader",
    },
}

with open("stats_queries.sql") as f:
    sql_text = f.read()

queries = parse_queries(sql_text)
stats_results = []
issues_results = []

for database, params in DATABASES.items():
    engine = make_engine(params)
    try:
        with engine.connect() as conn:
            print(f"[OK]   {database} — connected")
            tables = pd.read_sql_query(""" 
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'lbm' AND TABLE_TYPE = 'BASE TABLE'
            """, conn)
            tables = tables['table_name'].tolist()

            cols_df = pd.read_sql_query("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'lbm'
            """, conn)
            schema_info = cols_df.groupby("table_name")["column_name"].apply(list).to_dict()

            for table in tables:
                print("Fetching stats for table: ", table)
                columns = schema_info.get(table, [])

                for query_def in queries:
                    if all(col in columns for col in query_def["requires"]):
                        try:
                            query = query_def["sql"].format(table=table)
                            query_type = query_def["type"]
                            result = pd.read_sql_query(query, conn)
                            result["database"] = database
                            result["table"] = table
                            result["stat"] = query_def["name"]

                            if query_type == "stat":
                                stats_results.append(result)

                            elif query_type == "features":
                                result["geometry"] = result["the_geom"].apply(decode_geom)
                                result = result.drop(columns=["the_geom"])
                                issues_results.append(result)

                        except Exception as e:
                            print(f"Error executing {query_def['name']} on {table}: {e}")
    except Exception as e:
        print(f"[FAIL] {database} — {e}")
    finally:
        engine.dispose()

# ---------------------------------------------------------
# Process Stats (CSV / Dashboard)
# ---------------------------------------------------------
# Drop empty dataframes from the list to prevent the FutureWarning
stats_results = [
    df.dropna(how="all")
    for df in stats_results
]
stats_results = [df for df in stats_results if not df.empty]
issues_results = [
    df.dropna(axis=1, how="all")
    for df in issues_results
    if not df.dropna(axis=1, how="all").empty
]

if stats_results:
    stats_df = pd.concat(stats_results, ignore_index=True)
    stats_df.to_csv(stats_path, index=False)
    print("Stats CSV generated successfully.")

    if issues_results:
        issues_df = pd.concat(issues_results, ignore_index=True)
        if not issues_df.empty:
            issues_gdf = gpd.GeoDataFrame(issues_df, geometry="geometry", crs="EPSG:3857")
            issues_gdf.to_file(geom_path, driver="GeoJSON")
            print("Issues GeoJSON generated successfully.")
            build_dashboard(stats_df,issues_gdf, output_path=dashboard_path)
    else:
        print("No features outside bbox found (GeoJSON not generated).")
        build_dashboard(stats_df, output_path=dashboard_path)
else:
    print("No statistics data found to process.")

end_time = time.time()
duration = end_time-start_time
print(duration)

