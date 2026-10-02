import os
from datetime import datetime, timedelta
import requests

_BASE = "https://graph.facebook.com/v21.0"

# Compras: SOLO omni_purchase. "purchase", "omni_purchase" y
# "offsite_conversion.fb_pixel_purchase" reportan la MISMA venta por canales de
# conteo distintos — sumarlos triplica compras y revenue. omni_purchase es lo
# que el Ads Manager muestra como "Compras", ya deduplicado. Validado contra el
# panel el 2026-07-14 (2.361 compras reales vs 7.113 que reportaba la suma).
_PURCHASE_ACTIONS = {"omni_purchase"}

# Presets nativos de Meta para períodos comunes
_DATE_PRESETS = {7: "last_7d", 14: "last_14d", 30: "last_30d"}


def _token() -> str:
    t = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not t:
        raise ValueError("Variable de entorno META_ACCESS_TOKEN no definida")
    return t


def _account() -> str:
    acc = os.environ.get("META_AD_ACCOUNT_ID", "").strip()
    if not acc:
        raise ValueError("Variable de entorno META_AD_ACCOUNT_ID no definida")
    # Normalizar: la API requiere "act_{id}"
    return acc if acc.startswith("act_") else f"act_{acc}"


def _date_params(days: int) -> dict:
    if days in _DATE_PRESETS:
        return {"date_preset": _DATE_PRESETS[days]}
    end = datetime.today()
    start = end - timedelta(days=days - 1)
    return {
        "time_range": f'{{"since":"{start.strftime("%Y-%m-%d")}","until":"{end.strftime("%Y-%m-%d")}"}}'
    }


def _get(path: str, params: dict) -> dict:
    """GET con manejo de errores de Meta y paginación (sigue paging.next hasta agotar)."""
    params["access_token"] = _token()
    url = f"{_BASE}/{path}"
    result = None

    while url:
        resp = requests.get(url, params=params, timeout=20)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise

        if "error" in data:
            err = data["error"]
            code = err.get("code")
            msg = err.get("message", str(err))
            if code == 190:
                raise RuntimeError(f"(#190) Token de Meta expirado o inválido. Regenerar. Detalle: {msg}")
            if code == 200:
                raise RuntimeError(f"(#200) Permisos insuficientes sobre la cuenta publicitaria. Detalle: {msg}")
            raise RuntimeError(f"(#{code}) {msg}")

        if result is None:
            result = data
        else:
            result["data"] = result.get("data", []) + data.get("data", [])

        url = data.get("paging", {}).get("next")
        params = {}  # `next` ya incluye todos los parámetros en la URL

    return result


def _sum_action(actions: list, action_values: list, keys: set) -> tuple[float, float]:
    """Suma conversiones y revenue para las action_types que coincidan con `keys`."""
    convs = sum(
        float(a.get("value", 0))
        for a in actions
        if a.get("action_type") in keys
    )
    revenue = sum(
        float(v.get("value", 0))
        for v in action_values
        if v.get("action_type") in keys
    )
    return convs, revenue


def fetch_campaigns(days: int = 30) -> list[dict]:
    """Campañas con inversión, impresiones, clics, conversiones y ROAS."""
    params = {
        "level": "campaign",
        "fields": "campaign_id,campaign_name,spend,impressions,clicks,cpm,cpc,actions,action_values",
        **_date_params(days),
    }
    data = _get(f"{_account()}/insights", params)

    campaigns = []
    for row in data.get("data", []):
        actions = row.get("actions") or []
        action_values = row.get("action_values") or []
        convs, revenue = _sum_action(actions, action_values, _PURCHASE_ACTIONS)

        spend = float(row.get("spend", 0))
        roas = round(revenue / spend, 2) if spend > 0 else 0.0

        campaigns.append({
            "id":          row.get("campaign_id", ""),
            "name":        row.get("campaign_name", ""),
            "spend":       round(spend, 2),
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "cpm":         round(float(row.get("cpm", 0)), 2),
            "cpc":         round(float(row.get("cpc", 0)), 2),
            "conversions": round(convs, 1),
            "revenue":     round(revenue, 2),
            "roas":        roas,
            "days":        days,
        })

    campaigns.sort(key=lambda c: c["spend"], reverse=True)
    return campaigns


def fetch_summary(days: int = 30) -> dict:
    """Totales de cuenta: inversión, conversiones, ROAS, CPM y CPC."""
    params = {
        "level": "account",
        "fields": "spend,impressions,clicks,cpm,cpc,actions,action_values",
        **_date_params(days),
    }
    data = _get(f"{_account()}/insights", params)

    rows = data.get("data", [])
    if not rows:
        return {
            "spend": 0, "impressions": 0, "clicks": 0,
            "cpm": 0, "cpc": 0, "conversions": 0,
            "revenue": 0, "roas": 0, "days": days,
        }

    row = rows[0]
    actions = row.get("actions") or []
    action_values = row.get("action_values") or []
    convs, revenue = _sum_action(actions, action_values, _PURCHASE_ACTIONS)

    spend = float(row.get("spend", 0))
    return {
        "spend":       round(spend, 2),
        "impressions": int(row.get("impressions", 0)),
        "clicks":      int(row.get("clicks", 0)),
        "cpm":         round(float(row.get("cpm", 0)), 2),
        "cpc":         round(float(row.get("cpc", 0)), 2),
        "conversions": round(convs, 1),
        "revenue":     round(revenue, 2),
        "roas":        round(revenue / spend, 2) if spend > 0 else 0.0,
        "days":        days,
    }
