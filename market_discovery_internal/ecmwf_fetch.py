# market_discovery_internal/ecmwf_fetch.py
"""ECMWF Open Data fetcher (CC BY 4.0). 51-member ensemble, GRIB2 decode,
bilinear interpolation to station coordinates. Same data source as Polymarket
market makers. Requires: ecmwf-opendata, eccodes, xarray, cfgrib.

When those packages are not installed, the module gracefully returns None
and the caller falls back to Open-Meteo ensemble.
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from ecmwf.opendata import Client as ECMWFClient
    ECMWF_AVAILABLE = True
except ImportError:
    ECMWF_AVAILABLE = False

try:
    import eccodes
    import xarray as xr
    import cfgrib
    GRIB_AVAILABLE = True
except ImportError:
    GRIB_AVAILABLE = False


def bilinear_interp(grid, lat_idx, lon_idx):
    """Bilinear interpolation from 2D grid to fractional indices."""
    i = max(0, min(int(math.floor(lat_idx)), len(grid) - 2))
    j = max(0, min(int(math.floor(lon_idx)), len(grid[0]) - 2))
    di, dj = lat_idx - i, lon_idx - j
    v00, v01 = grid[i][j], grid[i][j + 1]
    v10, v11 = grid[i + 1][j], grid[i + 1][j + 1]
    return (1-di)*(1-dj)*v00 + (1-di)*dj*v01 + di*(1-dj)*v10 + di*dj*v11


def compute_grid_indices(lat, lon, lat0, lon0, grid_res):
    """Convert lat/lon to fractional grid indices."""
    return (lat - lat0) / grid_res, (lon - lon0) / grid_res


def fetch_ecmwf_ensemble_forecast(city, date_str, lat, lon):
    """Fetch ECMWF 51-member ensemble for a station. Returns dict with mean, std, members, or None."""
    if not ECMWF_AVAILABLE or not GRIB_AVAILABLE:
        logger.debug("[ECMWF] Not available — install ecmwf-opendata + eccodes + cfgrib")
        return None
    try:
        import tempfile, os
        client = ECMWFClient(source="ecmwf")
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            client.retrieve(stream="enfo", type="ef", param="mx2t", step=24, target=tmp_path)
            ds = xr.open_dataset(tmp_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"shortName": "mx2t"}})
            lats, lons = ds.latitude.values, ds.longitude.values
            grid_res = abs(lats[1] - lats[0]) if len(lats) > 1 else 0.25
            lat_idx, lon_idx = compute_grid_indices(lat, lon, lats[0], lons[0], grid_res)
            members = []
            if "number" in ds.dims:
                # Get the data variable name dynamically (mx2t, t2m, etc.)
                _var_name = list(ds.data_vars)[0] if len(ds.data_vars) > 0 else "t2m"
                for idx in range(len(ds.number)):
                    grid = ds.isel(number=idx).isel(step=0)[_var_name].values
                    if grid.ndim == 2:
                        members.append(float(bilinear_interp(grid.tolist(), lat_idx, lon_idx)) - 273.15)
            if len(members) < 5:
                return None
            mean_t = sum(members) / len(members)
            std_t = math.sqrt(sum((m - mean_t)**2 for m in members) / max(1, len(members) - 1))
            from market_discovery_internal.database_manager import db
            db.save_ecmwf_ensemble(city, date_str, [{"member_id": i, "temp_c": round(m, 2)} for i, m in enumerate(members)])
            logger.info("[ECMWF] %s %s: %d members, mean=%.1fC, std=%.2fC", city, date_str, len(members), mean_t, std_t)
            return {"mean": round(mean_t, 2), "std": round(std_t, 2), "member_count": len(members), "members": [round(m, 2) for m in members], "source": "ecmwf_opendata"}
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.warning("[ECMWF] fetch failed for %s %s: %s", city, date_str, e)
        return None
