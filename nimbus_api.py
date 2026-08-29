import os
import requests
from datetime import datetime

NIMBUS_API_TOKEN = os.getenv('NIMBUS_API_TOKEN')  # npk_...
NIMBUS_API_SECRET = os.getenv('NIMBUS_API_SECRET')  # Txnm_...
NIMBUS_BASE_URL = 'https://api-v2.nimbuspost.com/v2'

# Default pickup/warehouse details (from env vars)
WAREHOUSE_NAME = os.getenv('WAREHOUSE_NAME', 'Warehouse1 Sindewahi')
WAREHOUSE_ADDRESS = os.getenv('WAREHOUSE_ADDRESS', '')
WAREHOUSE_CITY = os.getenv('WAREHOUSE_CITY', 'Nagpur')
WAREHOUSE_STATE = os.getenv('WAREHOUSE_STATE', 'Maharashtra')
WAREHOUSE_PINCODE = os.getenv('WAREHOUSE_PINCODE', '441222')
WAREHOUSE_PHONE = os.getenv('WAREHOUSE_PHONE', '')

def _headers():
    return {
        'x-api-key': NIMBUS_API_TOKEN or '',
        'x-api-secret': NIMBUS_API_SECRET or '',
        'Content-Type': 'application/json'
    }

def is_configured():
    """Check if NimbusPost is properly configured"""
    return bool(NIMBUS_API_TOKEN and NIMBUS_API_SECRET)


def check_serviceability(delivery_pincode, weight_kg=0.5, payment_mode='prepaid', order_value_paise=50000):
    """
    Check if delivery is available to a given pincode.
    Returns: dict with 'available' (bool), 'couriers' (list of options)
    """
    if not is_configured():
        return {'available': True, 'couriers': [], 'message': 'NimbusPost not configured, allowing all pincodes'}

    try:
        url = f'{NIMBUS_BASE_URL}/serviceability'
        payload = {
            'pickupPincode': WAREHOUSE_PINCODE,
            'deliveryPincode': str(delivery_pincode),
            'paymentMode': payment_mode.lower(),
            'orderValuePaise': order_value_paise,
            'packages': [{
                'weight': int(weight_kg * 1000),  # v2 expects grams
                'length': 15,
                'width': 10,
                'height': 5
            }]
        }
        
        resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('success'):
            available_couriers = data.get('data', {}).get('available', [])
            
            # Map v2 response to our internal format
            mapped_couriers = []
            for c in available_couriers:
                rate_paise = c.get('result', {}).get('totalPaise', 0)
                mapped_couriers.append({
                    'courier_company_id': c.get('courierId'),
                    'courier_name': c.get('courierDisplayName', c.get('courierName')),
                    'rate': rate_paise / 100,  # Convert paise to rupees
                    'estimated_delivery_days': str(c.get('tatDays', 7))
                })
                
            return {
                'available': len(mapped_couriers) > 0,
                'couriers': mapped_couriers,
                'message': 'Delivery available' if mapped_couriers else 'No couriers available for this pincode'
            }
            
        # If API returns an error (like invalid pincode), fallback to Flat Rate so checkout doesn't break
        print(f"NimbusPost API v2 error: {data}. Falling back to Flat Rate.")
        return {
            'available': True,
            'couriers': [{
                'courier_name': 'Standard Delivery (Fallback)',
                'rate': 40,
                'estimated_delivery': '3-7 days'
            }],
            'message': 'Fallback to Standard Delivery'
        }
    except Exception as e:
        print(f'NimbusPost serviceability error: {e}')
        return {'available': True, 'couriers': [], 'message': 'Could not verify, allowing order'}


def get_shipping_rates(delivery_pincode, weight_kg=0.5):
    """
    Get shipping rates for a delivery pincode.
    Returns: list of {courier_name, rate, estimated_days}
    """
    result = check_serviceability(delivery_pincode, weight_kg)
    rates = []

    for courier in result.get('couriers', []):
        rates.append({
            'courier_id': courier.get('courier_company_id', ''),
            'courier_name': courier.get('courier_name', 'Standard Delivery'),
            'rate': courier.get('rate', 0),
            'estimated_days': courier.get('estimated_delivery_days', '5'),
            'min_weight': courier.get('min_weight', 0.5)
        })

    # Sort by cheapest
    rates.sort(key=lambda x: x.get('rate', 999))

    # If no rates from API (not configured or error), provide a default
    if not rates:
        rates = [{
            'courier_id': 'default',
            'courier_name': 'Standard Delivery',
            'rate': 60,  # Default ₹60 shipping
            'estimated_days': '5-7',
            'min_weight': 0.5
        }]

    return rates


def create_shipment(order_data):
    """
    Create a shipment on NimbusPost API v2.
    order_data should contain:
        - order_number
        - consignee (name, address, city, state, pincode, phone)
        - items (list of {name, quantity, price})
        - total_amount
        - weight_kg
        - courier_id (optional, for specific courier)
    Returns: dict with awb_number, courier_name, tracking_url, label_url
    """
    if not is_configured():
        return {
            'success': False,
            'message': 'NimbusPost not configured. Please add API keys to environment variables.',
            'awb_number': None
        }

    try:
        # Step 1: Create Order
        orders_url = f'{NIMBUS_BASE_URL}/orders'
        
        # Format items for v2
        mapped_items = []
        for item in order_data.get('items', []):
            mapped_items.append({
                'name': item['name'],
                'quantity': item['quantity'],
                'price': item['price']  # already in rupees by the time it reaches here
            })
            
        # Determine warehouse ID mapping for v2
        wh_name = order_data.get('pickup_location', WAREHOUSE_NAME)
        wh_id = 'WH-001' # default
        if 'Hyderabad' in wh_name:
            wh_id = 'WH-002'
            
        order_payload = {
            'order_number': order_data['order_number'],
            'order_type': 'b2c', # NimbusPost v2 accepts: b2c | document | reverse
            'payment_mode': 'prepaid',
            'warehouse_id': wh_id,
            'shipping_address': {
                'name': order_data['consignee']['name'],
                'address': order_data['consignee']['address'],
                'city': order_data['consignee']['city'],
                'state': order_data['consignee']['state'],
                # NimbusPost v2 expects both pincode and phone as numeric values,
                # not strings -- defensively strip any stray non-digit characters
                # (spaces, '+91', etc.) before converting either one.
                'pincode': int(''.join(filter(str.isdigit, str(order_data['consignee']['pincode'])))),
                'phone': int(''.join(filter(str.isdigit, str(order_data['consignee']['phone']))))
            },
            'items': mapped_items,
            'package': {
                # NimbusPost v2 expects weight in KILOGRAMS, not grams -- confirmed
                # by a live error where a real 0.22kg order was rejected as
                # "220000" against a "32000" (32kg) limit, only explainable if our
                # previous grams value (220) was being read as 220 kilograms.
                'weight': order_data.get('weight_kg', 0.5),
                'length': 15,
                'width': 10,
                'height': 5
            }
        }

        order_resp = requests.post(orders_url, json=order_payload, headers=_headers(), timeout=15)
        order_data_resp = order_resp.json()

        if order_resp.status_code not in [200, 201] or not order_data_resp.get('success'):
            return {
                'success': False,
                'message': str(order_data_resp),
                'awb_number': None
            }
            
        # Extract the created order_id
        nimbus_order_id = order_data_resp['data']['order_id']
        
        # Step 2: Book Shipment (Assign Courier & generate AWB)
        book_url = f'{NIMBUS_BASE_URL}/shipments/book'
        book_payload = {
            'order_id': nimbus_order_id
        }
        
        book_resp = requests.post(book_url, json=book_payload, headers=_headers(), timeout=15)
        book_data_resp = book_resp.json()
        
        if book_resp.status_code in [200, 201] and book_data_resp.get('success'):
            shipment = book_data_resp.get('data', {})
            return {
                'success': True,
                'awb_number': shipment.get('awb'),
                'courier_name': shipment.get('courier_name'),
                'tracking_url': f"https://ship.nimbuspost.com/tracking/{shipment.get('awb')}",
                # v2's booking response returns the label under 'label_url', not 'label' --
                # confirmed against the partner API v2 spec (POST /v2/shipments/book).
                'label_url': shipment.get('label_url', ''),
                'nimbus_order_id': nimbus_order_id,
                'message': 'Shipment created successfully'
            }
            
        return {
            'success': False,
            'message': str(book_data_resp),
            'awb_number': None
        }

    except Exception as e:
        print(f'NimbusPost v2 create shipment error: {e}')
        return {'success': False, 'message': str(e), 'awb_number': None}


def generate_label(nimbus_order_id):
    """
    Generate/fetch the shipping label PDF for an already-booked order.
    Used to backfill label_url for orders shipped before this app started
    saving nimbus_order_id, or if the booking response didn't include one.
    Returns: dict with 'success' and 'label_url' (or 'message' on failure).
    """
    if not is_configured() or not nimbus_order_id:
        return {'success': False, 'message': 'NimbusPost not configured or no order id available'}

    try:
        url = f'{NIMBUS_BASE_URL}/shipments/labels'
        resp = requests.post(url, json={'ids': [nimbus_order_id]}, headers=_headers(), timeout=15)
        data = resp.json()

        if resp.status_code == 200 and data.get('success'):
            label = data.get('data', {})
            return {'success': True, 'label_url': label.get('url', '')}

        return {'success': False, 'message': str(data)}
    except Exception as e:
        print(f'NimbusPost generate label error: {e}')
        return {'success': False, 'message': str(e)}


def track_shipment(awb_number):
    """
    Track a shipment by AWB number.
    Returns: dict with current_status, status_history, estimated_delivery
    """
    if not is_configured() or not awb_number:
        return {
            'success': False,
            'message': 'Tracking not available',
            'current_status': 'Unknown',
            'history': []
        }

    try:
        url = f'{NIMBUS_BASE_URL}/tracking/{awb_number}'
        resp = requests.get(url, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('success'):
            tracking = data.get('data', {})
            # v2's tracking-by-AWB response nests these under 'shipment' and
            # 'latest' with camelCase keys -- confirmed against the partner API
            # v2 spec, which does NOT include a multi-event 'history' array at
            # all (only the single latest event; full history needs the
            # tracking.updated webhook), so this endpoint can only ever surface
            # one timeline entry.
            shipment = tracking.get('shipment') or {}
            latest = tracking.get('latest') or {}

            estimated_delivery = 'N/A'
            edd_raw = shipment.get('edd')
            if edd_raw:
                try:
                    estimated_delivery = datetime.fromisoformat(edd_raw.replace('Z', '+00:00')).strftime('%d %b %Y')
                except (ValueError, TypeError):
                    estimated_delivery = edd_raw

            history = []
            if latest.get('message') or latest.get('shipStatus'):
                event_date = latest.get('eventTime', '')
                if event_date:
                    try:
                        event_date = datetime.fromisoformat(event_date.replace('Z', '+00:00')).strftime('%d %b %Y, %I:%M %p')
                    except (ValueError, TypeError):
                        pass
                history = [{
                    'status': latest.get('message') or latest.get('shipStatus', ''),
                    'location': latest.get('location', ''),
                    'date': event_date
                }]

            return {
                'success': True,
                'current_status': latest.get('shipStatus') or tracking.get('orderStatus', 'In Transit'),
                'estimated_delivery': estimated_delivery,
                'courier_name': shipment.get('courierName', ''),
                'history': history,
                'message': 'Tracking data retrieved'
            }
        return {
            'success': False,
            'message': str(data),
            'current_status': 'Unknown',
            'history': []
        }
    except Exception as e:
        print(f'NimbusPost tracking error: {e}')
        return {'success': False, 'message': str(e), 'current_status': 'Unknown', 'history': []}


def cancel_shipment(awb_number):
    """
    Cancel a shipment by AWB number (only before pickup).
    Returns: dict with success status
    """
    if not is_configured() or not awb_number:
        return {'success': False, 'message': 'Cannot cancel'}

    try:
        url = f'{NIMBUS_BASE_URL}/shipments/cancel'
        payload = {'awbs': [awb_number]}
        resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('success'):
            return {'success': True, 'message': 'Shipment cancelled'}
        return {'success': False, 'message': str(data)}
    except Exception as e:
        print(f'NimbusPost cancel error: {e}')
        return {'success': False, 'message': str(e)}
