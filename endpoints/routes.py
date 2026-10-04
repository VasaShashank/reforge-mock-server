import json
import os
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request


api_bp = Blueprint("api", __name__)


# ============================================================
# REFORGE MOCK SERVER
# ============================================================

MOCK_API_KEY = os.getenv("MOCK_API_KEY", "reforge-demo-key")

# In-memory state for the demo.
# Vercel may reset this between cold starts, so important demo
# behavior is also deterministic from IDs/scenarios.
shipments = {}
service_orders = {}
tracking_calls = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def authorized():
    """
    Accept the API key styles commonly used by generic connectors.
    If no key is supplied, allow the request so we can test manually.
    """
    supplied = (
        request.headers.get("X-API-Key")
        or request.headers.get("x-api-key")
        or request.headers.get("Authorization")
    )

    if not supplied:
        return True

    if supplied.startswith("Bearer "):
        supplied = supplied.replace("Bearer ", "", 1)

    if supplied.startswith("Token "):
        supplied = supplied.replace("Token ", "", 1)

    return supplied == MOCK_API_KEY


def auth_error():
    return jsonify(
        {
            "success": False,
            "error": "Unauthorized",
            "message": "Invalid API key",
        }
    ), 401


def scenario_from_request(default="HAPPY_PATH"):
    """
    Scenario can be supplied through:
      - X-ReForge-Scenario header
      - ?scenario=...
      - order ID / machine ID containing scenario keywords

    This makes evaluation testing easy without changing the API contract.
    """
    scenario = (
        request.headers.get("X-ReForge-Scenario")
        or request.args.get("scenario")
        or ""
    )

    return scenario.upper().strip() or default


def scenario_from_text(text, default="HAPPY_PATH"):
    text = (text or "").upper()

    known = [
        "LOW_BALANCE",
        "DUPLICATE",
        "TIMEOUT",
        "MALFORMED",
        "TECHNICIAN_UNAVAILABLE",
        "REPAIR_FAILED",
        "VERIFICATION_FAILED",
        "NSZ",
        "EMBARGO",
    ]

    for value in known:
        if value in text:
            return value

    return default


# ============================================================
# ORIGINAL SAMPLE ENDPOINTS
# ============================================================

@api_bp.get("/api/data")
def get_sample_data():
    return jsonify(
        {
            "data": [
                {"id": 1, "name": "Sample Item 1", "value": 100},
                {"id": 2, "name": "Sample Item 2", "value": 200},
                {"id": 3, "name": "Sample Item 3", "value": 300},
            ],
            "total": 3,
            "timestamp": now(),
        }
    )


@api_bp.get("/api/items/<int:item_id>")
def get_item(item_id: int):
    return jsonify(
        {
            "item": {
                "id": item_id,
                "name": f"Sample Item {item_id}",
                "value": item_id * 100,
            },
            "timestamp": now(),
        }
    )


# ============================================================
# DELHIVERY: PINCODE SERVICEABILITY
# Exact documented route:
# GET /c/api/pin-codes/json/?filter_codes=<pin>
# ============================================================

@api_bp.get("/c/api/pin-codes/json/")
def pincode_serviceability():

    if not authorized():
        return auth_error()

    pin = request.args.get("filter_codes", "").strip()

    if not pin:
        return jsonify(
            {
                "success": False,
                "error": "filter_codes is required",
            }
        ), 400

    # Deterministic demo cases.
    #
    # 560001 -> serviceable
    # 560002 -> embargo
    # 560003 -> non-serviceable / empty list
    # Any other valid PIN -> serviceable

    if pin == "560003":
        # Official behavior described by Delhivery:
        # empty list means non-serviceable.
        return jsonify([])

    if pin == "560002":
        return jsonify(
            [
                {
                    "pincode": pin,
                    "remark": "Embargo",
                }
            ]
        )

    return jsonify(
        [
            {
                "pincode": pin,
                "remark": "",
            }
        ]
    )


# ============================================================
# DELHIVERY: SHIPMENT MANIFESTATION
# Exact documented route:
# POST /api/cmu/create.json
# ============================================================

def parse_delhivery_manifest():

    # Support JSON body.
    if request.is_json:
        body = request.get_json(silent=True) or {}

        # Some callers may send:
        # {"data": "...JSON string..."}
        if isinstance(body.get("data"), str):
            try:
                body = json.loads(body["data"])
            except json.JSONDecodeError:
                pass

        return body

    # Support documented form-style:
    # format=json&data={...}
    data = request.form.get("data")

    if data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    # Last fallback: raw body containing JSON.
    raw = request.get_data(as_text=True)

    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    return None


@api_bp.post("/api/cmu/create.json")
def shipment_creation():

    if not authorized():
        return auth_error()

    body = parse_delhivery_manifest()

    if body is None:
        return jsonify(
            {
                "success": False,
                "error": "Invalid shipment payload",
                "message": "Expected JSON or form field data containing JSON",
            }
        ), 400

    shipments_payload = body.get("shipments", [])
    pickup_location = body.get("pickup_location", {})

    if not shipments_payload:
        return jsonify(
            {
                "success": False,
                "error": "shipments is required",
            }
        ), 400

    if not pickup_location:
        return jsonify(
            {
                "success": False,
                "error": "pickup_location is required",
            }
        ), 400

    first = shipments_payload[0]

    mandatory = [
        "name",
        "order",
        "phone",
        "add",
        "pin",
        "payment_mode",
    ]

    missing = [
        field
        for field in mandatory
        if first.get(field) in (None, "")
    ]

    if missing:
        return jsonify(
            {
                "success": False,
                "error": "ValidationError",
                "message": "Missing mandatory fields",
                "fields": missing,
            }
        ), 400

    order_id = str(first["order"])
    scenario = scenario_from_request(
        scenario_from_text(order_id)
    )

    # -------------------------
    # Failure simulations
    # -------------------------

    if scenario == "LOW_BALANCE":
        return jsonify(
            {
                "success": False,
                "error": "INSUFFICIENT_BALANCE",
                "message": "Insufficient balance to manifest shipment",
                "order": order_id,
            }
        ), 402

    if scenario == "TIMEOUT":
        return jsonify(
            {
                "success": False,
                "error": "UPSTREAM_TIMEOUT",
                "message": "Shipment manifestation timed out",
                "order": order_id,
            }
        ), 504

    if scenario == "MALFORMED":
        # Deliberately malformed/non-JSON response for resilience testing.
        return (
            '{"success":true,"shipment":',
            200,
            {"Content-Type": "application/json"},
        )

    if scenario == "DUPLICATE" or order_id in shipments:
        return jsonify(
            {
                "success": False,
                "error": "DUPLICATE_ORDER",
                "message": "Order ID already exists",
                "order": order_id,
            }
        ), 409

    # -------------------------
    # Successful manifestation
    # -------------------------

    waybill = f"REFG{int(time.time() * 1000)}"

    shipment = {
        "waybill": waybill,
        "order": order_id,
        "name": first["name"],
        "phone": first["phone"],
        "address": first["add"],
        "pin": str(first["pin"]),
        "payment_mode": first["payment_mode"],
        "shipping_mode": first.get("shipping_mode", "Surface"),
        "status": "Manifested",
        "created_at": now(),
        "tracking_history": [
            {
                "status": "Manifested",
                "timestamp": now(),
            }
        ],
    }

    shipments[order_id] = shipment

    return jsonify(
        {
            "success": True,
            "status": "Manifested",
            "order": order_id,
            "waybill": waybill,
            "shipment": shipment,
        }
    ), 200


# ============================================================
# DELHIVERY: SHIPMENT TRACKING
# Exact documented route:
# GET /api/v1/packages/json/?waybill=...&ref_ids=...
# ============================================================

@api_bp.get("/api/v1/packages/json/")
def shipment_tracking():

    if not authorized():
        return auth_error()

    waybill = request.args.get("waybill", "").strip()
    ref_id = request.args.get("ref_ids", "").strip()

    if not waybill and not ref_id:
        return jsonify(
            {
                "success": False,
                "error": "waybill is required",
            }
        ), 400

    shipment = None

    if ref_id:
        shipment = shipments.get(ref_id)

    if shipment is None and waybill:
        for item in shipments.values():
            if item["waybill"] == waybill:
                shipment = item
                break

    # Allow deterministic tracking even after a cold start.
    if shipment is None and waybill:
        shipment = {
            "waybill": waybill,
            "order": ref_id or "UNKNOWN",
            "status": "Manifested",
        }

    scenario = scenario_from_request(
        scenario_from_text(
            f"{waybill} {ref_id}"
        )
    )

    if scenario == "TIMEOUT":
        return jsonify(
            {
                "success": False,
                "error": "TRACKING_TIMEOUT",
                "message": "Tracking service timed out",
            }
        ), 504

    if scenario == "MALFORMED":
        return (
            '{"ShipmentData": [',
            200,
            {"Content-Type": "application/json"},
        )

    key = waybill or ref_id
    tracking_calls[key] = tracking_calls.get(key, 0) + 1

    call_number = tracking_calls[key]

    # First call: in transit.
    # Second call onward: delivered.
    if call_number <= 1:
        status = "In Transit"
    else:
        status = "Delivered"

    if shipment is not None and shipment.get("status"):
        shipment["status"] = status

    history = [
        {
            "status": "Manifested",
            "timestamp": now(),
        },
        {
            "status": status,
            "timestamp": now(),
        },
    ]

    return jsonify(
        {
            "success": True,
            "waybill": waybill,
            "ref_ids": ref_id,
            "status": status,
            "tracking_history": history,
            "shipment": shipment,
        }
    ), 200


# ============================================================
# REFORGE: MACHINE LOOKUP
# ============================================================

@api_bp.get("/reforge/machines/<machine_id>")
def get_machine(machine_id):

    if not authorized():
        return auth_error()

    machine_upper = machine_id.upper()

    return jsonify(
        {
            "success": True,
            "machine": {
                "machine_id": machine_id,
                "type": "Industrial Freezer",
                "customer": "Demo Manufacturing Plant",
                "location": "Plant A",
                "status": "NOT_COOLING",
                "temperature_celsius": -5,
                "target_temperature_celsius": -18,
                "reported_problem": "Freezer is not cooling",
                "scenario": (
                    "VERIFICATION_FAILED"
                    if "FAILVERIFY" in machine_upper
                    else "HAPPY_PATH"
                ),
            },
        }
    )


# ============================================================
# REFORGE: DIAGNOSIS
# ============================================================

@api_bp.post("/reforge/machines/<machine_id>/diagnose")
def diagnose_machine(machine_id):

    if not authorized():
        return auth_error()

    body = request.get_json(silent=True) or {}

    symptoms = body.get(
        "symptoms",
        "Machine is not reaching target temperature",
    )

    machine_upper = machine_id.upper()
    text = f"{machine_upper} {json.dumps(body)}".upper()

    if "AMBIGUOUS" in text:
        return jsonify(
            {
                "success": True,
                "diagnosis": None,
                "confidence": 0.61,
                "requires_human": True,
                "reason": "Symptoms are materially ambiguous",
                "recommended_action": "Collect additional diagnostic information",
            }
        )

    if "COMPRESSOR" in text:
        diagnosis = "Compressor failure"
        confidence = 0.91
        part = "COMPRESSOR_01"
    else:
        diagnosis = "Condenser fan failure"
        confidence = 0.94
        part = "CONDENSER_FAN_01"

    return jsonify(
        {
            "success": True,
            "machine_id": machine_id,
            "symptoms": symptoms,
            "diagnosis": diagnosis,
            "confidence": confidence,
            "repair_required": True,
            "part_required": part,
            "estimated_repair_minutes": 90,
        }
    )


# ============================================================
# REFORGE: TECHNICIAN AVAILABILITY
# ============================================================

@api_bp.get("/reforge/technicians/availability")
def technician_availability():

    if not authorized():
        return auth_error()

    scenario = scenario_from_request(
        scenario_from_text(
            request.args.get("machine_id", "")
        )
    )

    if scenario == "TECHNICIAN_UNAVAILABLE":
        return jsonify(
            {
                "success": True,
                "available": False,
                "reason": "No qualified technician currently available",
            }
        )

    return jsonify(
        {
            "success": True,
            "available": True,
            "technician": {
                "technician_id": "TECH-102",
                "name": "Demo Field Technician",
                "skill": "Industrial Refrigeration",
                "eta_minutes": 45,
            },
        }
    )


# ============================================================
# REFORGE: SERVICE ORDER CREATION
# ============================================================

@api_bp.post("/reforge/service-orders")
def create_service_order():

    if not authorized():
        return auth_error()

    body = request.get_json(silent=True) or {}

    machine_id = body.get("machine_id")

    if not machine_id:
        return jsonify(
            {
                "success": False,
                "error": "machine_id is required",
            }
        ), 400

    text = json.dumps(body).upper()
    scenario = scenario_from_request(
        scenario_from_text(text)
    )

    if scenario == "TECHNICIAN_UNAVAILABLE":
        return jsonify(
            {
                "success": False,
                "error": "NO_TECHNICIAN_AVAILABLE",
                "message": "No qualified technician is available",
            }
        ), 409

    order_id = f"SRV-{int(time.time() * 1000)}"

    service_orders[order_id] = {
        "service_order_id": order_id,
        "machine_id": machine_id,
        "technician_id": body.get("technician_id", "TECH-102"),
        "status": "DISPATCHED",
        "scenario": scenario,
        "created_at": now(),
    }

    return jsonify(
        {
            "success": True,
            "service_order": service_orders[order_id],
        }
    ), 200


# ============================================================
# REFORGE: SERVICE ORDER STATUS
# ============================================================

@api_bp.get("/reforge/service-orders/<order_id>")
def get_service_order(order_id):

    if not authorized():
        return auth_error()

    order = service_orders.get(order_id)

    if order is None:
        return jsonify(
            {
                "success": False,
                "error": "SERVICE_ORDER_NOT_FOUND",
                "service_order_id": order_id,
            }
        ), 404

    scenario = order.get("scenario", "HAPPY_PATH")

    if scenario == "REPAIR_FAILED":
        status = "REPAIR_FAILED"
    else:
        status = "COMPLETED"

    order["status"] = status
    order["updated_at"] = now()

    return jsonify(
        {
            "success": True,
            "service_order": order,
        }
    )


# ============================================================
# REFORGE: MACHINE VERIFICATION
# ============================================================

@api_bp.post("/reforge/machines/<machine_id>/verify")
def verify_machine(machine_id):

    if not authorized():
        return auth_error()

    body = request.get_json(silent=True) or {}

    machine_upper = machine_id.upper()
    text = json.dumps(body).upper()

    should_fail = (
        "FAILVERIFY" in machine_upper
        or "VERIFICATION_FAILED" in text
        or "REPAIR_FAILED" in text
    )

    if should_fail:
        return jsonify(
            {
                "success": True,
                "verified": False,
                "machine_id": machine_id,
                "temperature_celsius": -5,
                "target_temperature_celsius": -18,
                "status": "NOT_RECOVERED",
                "evidence": {
                    "compressor_running": False,
                    "temperature_stable": False,
                },
                "next_action": "Return to diagnosis and create a new repair plan",
            }
        )

    return jsonify(
        {
            "success": True,
            "verified": True,
            "machine_id": machine_id,
            "temperature_celsius": -18,
            "target_temperature_celsius": -18,
            "status": "NORMAL",
            "evidence": {
                "compressor_running": True,
                "temperature_stable": True,
                "cooling_confirmed": True,
            },
        }
    )
