import os
import json
import functools
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta
from flask import Flask, jsonify, send_from_directory, request, session, redirect
from flask_cors import CORS
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    Dimension,
    Metric,
    DateRange,
    FilterExpression,
    Filter,
    OrderBy,
)
from google.oauth2 import service_account
import google_ads as gads
import vtex as vtx
import merchant_center as mc
import search_console as sc
import clarity
import connectif
import meta_ads as meta

app = Flask(__name__, static_folder="static")
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

ADMIN_USER = "admin"
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "")
if not ADMIN_PASS:
    raise RuntimeError(
        "ADMIN_PASSWORD no está definida o está vacía en el entorno/.env. "
        "El servidor no arranca sin contraseña de admin."
    )

SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    os.path.join(os.path.dirname(__file__), "service-account.json"),
)
PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autorizado. Por favor iniciá sesión."}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def get_ga4_client():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def fetch_product_data(start_date="30daysAgo", end_date="today"):
    client = get_ga4_client()

    request_body = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[
            Dimension(name="itemName"),
            Dimension(name="itemCategory"),
            Dimension(name="itemBrand"),
        ],
        metrics=[
            Metric(name="itemsViewed"),
            Metric(name="itemsAddedToCart"),
            Metric(name="itemsPurchased"),
            Metric(name="itemRevenue"),
        ],
        limit=10000,
    )

    response = client.run_report(request_body)

    products = {}
    for row in response.rows:
        name = row.dimension_values[0].value or "(not set)"
        category = row.dimension_values[1].value or "(not set)"
        brand = row.dimension_values[2].value or "(not set)"

        key = name
        if key not in products:
            products[key] = {
                "name": name,
                "category": category,
                "brand": brand,
                "views": 0,
                "add_to_cart": 0,
                "purchases": 0,
                "revenue": 0.0,
            }

        products[key]["views"] += int(row.metric_values[0].value or 0)
        products[key]["add_to_cart"] += int(row.metric_values[1].value or 0)
        products[key]["purchases"] += int(row.metric_values[2].value or 0)
        products[key]["revenue"] += float(row.metric_values[3].value or 0)

    result = []
    for p in products.values():
        views = p["views"]
        purchases = p["purchases"]
        cart = p["add_to_cart"]

        view_to_purchase = round((purchases / views * 100), 2) if views > 0 else 0
        view_to_cart = round((cart / views * 100), 2) if views > 0 else 0
        cart_to_purchase = round((purchases / cart * 100), 2) if cart > 0 else 0
        lost_opportunity = views - purchases

        result.append({
            **p,
            "view_to_purchase_rate": view_to_purchase,
            "view_to_cart_rate": view_to_cart,
            "cart_to_purchase_rate": cart_to_purchase,
            "lost_opportunity": lost_opportunity,
            "revenue": round(p["revenue"], 2),
        })

    result.sort(key=lambda x: x["lost_opportunity"], reverse=True)
    return result


def _run_ga4_query(client, start_date, end_date):
    """Single GA4 report query, returns dict keyed by product name."""
    request_body = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[
            Dimension(name="itemName"),
            Dimension(name="itemCategory"),
            Dimension(name="itemBrand"),
        ],
        metrics=[
            Metric(name="itemsViewed"),
            Metric(name="itemsAddedToCart"),
            Metric(name="itemsPurchased"),
            Metric(name="itemRevenue"),
        ],
        limit=10000,
    )
    response = client.run_report(request_body)
    products = {}
    for row in response.rows:
        name = row.dimension_values[0].value or "(not set)"
        category = row.dimension_values[1].value or "(not set)"
        brand = row.dimension_values[2].value or "(not set)"
        if name not in products:
            products[name] = {"name": name, "category": category, "brand": brand,
                              "views": 0, "add_to_cart": 0, "purchases": 0, "revenue": 0.0}
        products[name]["views"] += int(row.metric_values[0].value or 0)
        products[name]["add_to_cart"] += int(row.metric_values[1].value or 0)
        products[name]["purchases"] += int(row.metric_values[2].value or 0)
        products[name]["revenue"] += float(row.metric_values[3].value or 0)
    return products


def fetch_product_comparison(start_date, end_date, prev_start, prev_end):
    """Fetch current and previous periods with two separate GA4 calls, merge and return deltas."""
    client = get_ga4_client()
    current = _run_ga4_query(client, start_date, end_date)
    previous = _run_ga4_query(client, prev_start, prev_end)

    def calc_rates(p):
        views, cart, purchases = p["views"], p["add_to_cart"], p["purchases"]
        return {
            **p,
            "view_to_purchase_rate": round(purchases / views * 100, 2) if views > 0 else 0,
            "view_to_cart_rate": round(cart / views * 100, 2) if views > 0 else 0,
            "cart_to_purchase_rate": round(purchases / cart * 100, 2) if cart > 0 else 0,
            "lost_opportunity": views - purchases,
            "revenue": round(p.get("revenue", 0), 2),
        }

    result = []
    all_names = set(current.keys()) | set(previous.keys())
    for name in all_names:
        cur = calc_rates(current[name]) if name in current else None
        prev = previous.get(name)

        if cur is None:
            continue

        prev_views = prev["views"] if prev else 0
        prev_purchases = prev["purchases"] if prev else 0
        prev_conv = round(prev_purchases / prev_views * 100, 2) if prev and prev["views"] > 0 else 0

        def delta(new, old):
            if old == 0:
                return None
            return round((new - old) / old * 100, 1)

        result.append({
            **cur,
            "views_prev": prev_views,
            "purchases_prev": prev_purchases,
            "view_to_purchase_rate_prev": prev_conv,
            "views_delta": delta(cur["views"], prev_views),
            "conv_delta": delta(cur["view_to_purchase_rate"], prev_conv),
        })

    result.sort(key=lambda x: x["lost_opportunity"], reverse=True)
    return result


def parse_ga4_date(ga4_date, reference_date=None):
    """Convert GA4 date strings like '30daysAgo' or 'today' to datetime objects."""
    if reference_date is None:
        reference_date = datetime.today()
    if ga4_date == "today":
        return reference_date
    if ga4_date == "yesterday":
        return reference_date - timedelta(days=1)
    if ga4_date.endswith("daysAgo"):
        days = int(ga4_date.replace("daysAgo", ""))
        return reference_date - timedelta(days=days)
    return datetime.strptime(ga4_date, "%Y-%m-%d")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect("/")
    if request.method == "POST":
        user = request.form.get("username", "")
        pwd = request.form.get("password", "")
        if user == ADMIN_USER and pwd == ADMIN_PASS:
            session["logged_in"] = True
            return redirect("/")
        return redirect("/login?error=1")
    return send_from_directory("static", "login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/product_trend")
@login_required
def api_product_trend():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nombre de producto requerido"}), 400
    try:
        client = get_ga4_client()
        request_body = RunReportRequest(
            property=f"properties/{PROPERTY_ID}",
            date_ranges=[DateRange(start_date="56daysAgo", end_date="today")],
            dimensions=[Dimension(name="yearWeek")],
            metrics=[
                Metric(name="itemsViewed"),
                Metric(name="itemsPurchased"),
                Metric(name="itemRevenue"),
            ],
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="itemName",
                    string_filter=Filter.StringFilter(
                        value=name,
                        match_type=Filter.StringFilter.MatchType.EXACT,
                        case_sensitive=True,
                    )
                )
            ),
            order_bys=[OrderBy(
                dimension=OrderBy.DimensionOrderBy(dimension_name="yearWeek"),
                desc=False,
            )],
            limit=100,
        )
        response = client.run_report(request_body)

        weeks, views, purchases, revenue = [], [], [], []
        for row in response.rows:
            yw = row.dimension_values[0].value
            year, week = int(yw[:4]), int(yw[4:])
            d = datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u")
            weeks.append(f"{d.day}/{d.month}")
            views.append(int(row.metric_values[0].value or 0))
            purchases.append(int(row.metric_values[1].value or 0))
            revenue.append(round(float(row.metric_values[2].value or 0), 2))

        return jsonify({"weeks": weeks, "views": views, "purchases": purchases, "revenue": revenue})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare")
@login_required
def api_compare():
    start_date = request.args.get("start_date", "30daysAgo")
    end_date = request.args.get("end_date", "today")
    try:
        today = datetime.today()
        start_dt = parse_ga4_date(start_date, today)
        end_dt = parse_ga4_date(end_date, today)
        period_days = (end_dt - start_dt).days + 1

        prev_end_dt = start_dt - timedelta(days=1)
        prev_start_dt = prev_end_dt - timedelta(days=period_days - 1)

        prev_start = prev_start_dt.strftime("%Y-%m-%d")
        prev_end = prev_end_dt.strftime("%Y-%m-%d")

        data = fetch_product_comparison(start_date, end_date, prev_start, prev_end)
        categories = sorted(set(p["category"] for p in data if p["category"] != "(not set)"))
        brands = sorted(set(p["brand"] for p in data if p["brand"] != "(not set)"))
        return jsonify({"products": data, "categories": categories, "brands": brands,
                        "prev_period": {"start": prev_start, "end": prev_end}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
@login_required
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/products")
@login_required
def api_products():
    start_date = request.args.get("start_date", "30daysAgo")
    end_date = request.args.get("end_date", "today")
    try:
        data = fetch_product_data(start_date, end_date)
        categories = sorted(set(p["category"] for p in data if p["category"] != "(not set)"))
        brands = sorted(set(p["brand"] for p in data if p["brand"] != "(not set)"))
        return jsonify({"products": data, "categories": categories, "brands": brands})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/campaigns")
@login_required
def api_campaigns():
    """Campañas activas de Google Ads con gasto, conversiones y ROAS."""
    try:
        days = int(request.args.get("days", 30))
        if days not in (7, 14, 30):
            days = 30
        campaigns = gads.fetch_campaigns(days)
        total_cost  = sum(c["cost"] for c in campaigns)
        total_conv  = sum(c["conversions"] for c in campaigns)
        total_value = sum(c["conversions_value"] for c in campaigns)
        summary = {
            "campaigns_active":        len(campaigns),
            "total_cost":              round(total_cost, 2),
            "total_conversions":       round(total_conv, 1),
            "total_conversions_value": round(total_value, 2),
            "roas":                    round(total_value / total_cost, 2) if total_cost > 0 else 0.0,
            "days":                    days,
        }
        return jsonify({"campaigns": campaigns, "summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/campaigns/summary")
@login_required
def api_campaigns_summary():
    """Totales agregados de cuenta: gasto, conversiones y ROAS."""
    try:
        days = int(request.args.get("days", 30))
        if days not in (7, 14, 30):
            days = 30
        return jsonify(gads.fetch_summary(days))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vtex/orders")
@login_required
def api_vtex_orders():
    try:
        days       = int(request.args.get("days", 30))
        with_items = request.args.get("with_items", "false").lower() == "true"
        orders = vtx.fetch_orders(days, with_items=with_items)
        return jsonify({"orders": orders, "total": len(orders), "days": days})
    except KeyError as e:
        return jsonify({"error": f"Variable de entorno faltante: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vtex/orders/enriched")
@login_required
def api_vtex_orders_enriched():
    try:
        days = int(request.args.get("days", 7))
        orders = vtx.fetch_orders(days, with_items=True)
        enriched = vtx.enrich_orders(orders)
        return jsonify({"orders": enriched, "total": len(enriched), "days": days})
    except KeyError as e:
        return jsonify({"error": f"Variable de entorno faltante: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vtex/products")
@login_required
def api_vtex_products():
    try:
        page      = int(request.args.get("page", 1))
        page_size = int(request.args.get("pageSize", 50))
        products  = vtx.fetch_products(page, page_size)
        return jsonify({"products": products, "page": page, "pageSize": page_size})
    except KeyError as e:
        return jsonify({"error": f"Variable de entorno faltante: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/merchant/products")
@login_required
def api_merchant_products():
    try:
        max_results = int(request.args.get("max_results", 50))
        products = mc.fetch_products(max_results=max_results)
        return jsonify({"products": products, "total": len(products)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/merchant/statuses")
@login_required
def api_merchant_statuses():
    try:
        statuses = mc.fetch_product_statuses()
        approved   = [s for s in statuses if s["status"] == "approved"]
        disapproved = [s for s in statuses if s["status"] == "disapproved"]
        with_issues = [s for s in statuses if s["issues_count"] > 0]
        return jsonify({
            "statuses": statuses,
            "total": len(statuses),
            "summary": {
                "approved":    len(approved),
                "disapproved": len(disapproved),
                "with_issues": len(with_issues),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/performance")
@login_required
def api_search_performance():
    try:
        days = max(1, int(request.args.get("days", 30)))
        data = sc.fetch_search_performance(days)
        return jsonify({"rows": data, "total": len(data), "days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/queries")
@login_required
def api_search_queries():
    try:
        days  = max(1, int(request.args.get("days", 30)))
        limit = max(1, int(request.args.get("limit", 20)))
        data  = sc.fetch_top_queries(days=days, limit=limit)
        return jsonify({"queries": data, "total": len(data), "days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/pages")
@login_required
def api_search_pages():
    try:
        days  = max(1, int(request.args.get("days", 30)))
        limit = max(1, int(request.args.get("limit", 20)))
        data  = sc.fetch_top_pages(days=days, limit=limit)
        return jsonify({"pages": data, "total": len(data), "days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clarity/summary")
@login_required
def api_clarity_summary():
    try:
        days = max(1, int(request.args.get("days", 7)))
        return jsonify(clarity.fetch_summary(days=days))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clarity/issues")
@login_required
def api_clarity_issues():
    try:
        days = max(1, int(request.args.get("days", 7)))
        pages = clarity.fetch_pages_with_issues(days=days)
        return jsonify({
            "pages": pages,
            "total": len(pages),
            "days": days,
            "cache": clarity.cache_info(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connectif/campaigns")
@login_required
def api_connectif_campaigns():
    try:
        limit = max(1, int(request.args.get("limit", 20)))
        campaigns = connectif.fetch_campaigns(limit=limit)
        return jsonify({"campaigns": campaigns, "total": len(campaigns)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connectif/contacts")
@login_required
def api_connectif_contacts():
    try:
        return jsonify(connectif.fetch_contacts_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connectif/automations")
@login_required
def api_connectif_automations():
    try:
        automations = connectif.fetch_automations()
        return jsonify({"automations": automations, "total": len(automations)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/meta/campaigns")
@login_required
def api_meta_campaigns():
    try:
        days = max(1, int(request.args.get("days", 30)))
        campaigns = meta.fetch_campaigns(days=days)
        return jsonify({"campaigns": campaigns, "total": len(campaigns), "days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/meta/summary")
@login_required
def api_meta_summary():
    try:
        days = max(1, int(request.args.get("days", 30)))
        return jsonify(meta.fetch_summary(days=days))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Starting Product Analyzer on http://localhost:5000")
    app.run(debug=True, port=5000)
