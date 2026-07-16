import os
import requests

NIMBUS_API_TOKEN = os.getenv('NIMBUS_API_TOKEN')
NIMBUS_BASE_URL = 'https://api.nimbuspost.com/v1'

# Default pickup/warehouse details (from env vars)
WAREHOUSE_NAME = os.getenv('WAREHOUSE_NAME', 'Heritage Spices')
WAREHOUSE_ADDRESS = os.getenv('WAREHOUSE_ADDRESS', '')
WAREHOUSE_CITY = os.getenv('WAREHOUSE_CITY', 'Nagpur')
WAREHOUSE_STATE = os.getenv('WAREHOUSE_STATE', 'Maharashtra')
WAREHOUSE_PINCODE = os.getenv('WAREHOUSE_PINCODE', '440001')
WAREHOUSE_PHONE = os.getenv('WAREHOUSE_PHONE', '')

NIMBUS_EMAIL = os.getenv('NIMBUS_EMAIL')
NIMBUS_PASSWORD = os.getenv('NIMBUS_PASSWORD')

_jwt_token = None

def get_token():
    global _jwt_token
    if _jwt_token:
        return _jwt_token
        
    if NIMBUS_EMAIL and NIMBUS_PASSWORD:
        try:
            url = f'{NIMBUS_BASE_URL}/users/login'
            payload = {'email': NIMBUS_EMAIL, 'password': NIMBUS_PASSWORD}
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get('status'):
                _jwt_token = data.get('data', {}).get('token')
                return _jwt_token
        except Exception as e:
            print(f"Nimbus login error: {e}")
            
    return NIMBUS_API_TOKEN

def _headers():
    token = get_token()
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

def is_configured():
    """Check if NimbusPost is properly configured"""
    return bool(get_token())


def check_serviceability(delivery_pincode, weight_kg=0.5, payment_mode='prepaid'):
    """
    Check if delivery is available to a given pincode.
    Returns: dict with 'available' (bool), 'couriers' (list of options)
    """
    if not is_configured():
        return {'available': True, 'couriers': [], 'message': 'NimbusPost not configured, allowing all pincodes'}

    try:
        url = f'{NIMBUS_BASE_URL}/courier/serviceability'
        payload = {
            'origin': WAREHOUSE_PINCODE,
            'destination': delivery_pincode,
            'weight': weight_kg,
            'payment_type': payment_mode
        }
        resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('status'):
            couriers = data.get('data', [])
            return {
                'available': len(couriers) > 0,
                'couriers': couriers,
                'message': 'Delivery available' if couriers else 'No couriers available for this pincode'
            }
        return {'available': False, 'couriers': [], 'message': data.get('message', 'Serviceability check failed')}
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
            'estimated_days': courier.get('estimated_delivery_days', '5-7'),
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
    Create a shipment on NimbusPost.
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
            'message': 'NimbusPost not configured. Please add NIMBUS_API_TOKEN to environment variables.',
            'awb_number': None
        }

    try:
        url = f'{NIMBUS_BASE_URL}/shipments'
        payload = {
            'order_number': order_data['order_number'],
            'shipping_customer_name': order_data['consignee']['name'],
            'shipping_address': order_data['consignee']['address'],
            'shipping_city': order_data['consignee']['city'],
            'shipping_state': order_data['consignee']['state'],
            'shipping_pincode': order_data['consignee']['pincode'],
            'shipping_country': 'India',
            'shipping_phone': order_data['consignee']['phone'],
            'order_amount': order_data['total_amount'],
            'payment_type': 'prepaid',
            'sub_total': order_data['total_amount'],
            'weight': order_data.get('weight_kg', 0.5),
            'length': order_data.get('length', 15),
            'breadth': order_data.get('breadth', 10),
            'height': order_data.get('height', 5),
            'pickup_location': WAREHOUSE_NAME,
            'order_items': []
        }

        for item in order_data.get('items', []):
            payload['order_items'].append({
                'name': item['name'],
                'qty': item['quantity'],
                'price': item['price']
            })

        resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
        data = resp.json()

        if resp.status_code in [200, 201] and data.get('status'):
            shipment = data.get('data', {})
            return {
                'success': True,
                'awb_number': shipment.get('awb_number'),
                'courier_name': shipment.get('courier_name'),
                'tracking_url': shipment.get('tracking_url', ''),
                'label_url': shipment.get('label', ''),
                'message': 'Shipment created successfully'
            }
        return {
            'success': False,
            'message': data.get('message', 'Failed to create shipment'),
            'awb_number': None
        }
    except Exception as e:
        print(f'NimbusPost create shipment error: {e}')
        return {'success': False, 'message': str(e), 'awb_number': None}


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
        url = f'{NIMBUS_BASE_URL}/shipments/track/{awb_number}'
        resp = requests.get(url, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('status'):
            tracking = data.get('data', {})
            history = tracking.get('history', [])
            return {
                'success': True,
                'current_status': tracking.get('current_status', 'In Transit'),
                'estimated_delivery': tracking.get('edd', 'N/A'),
                'courier_name': tracking.get('courier_name', ''),
                'history': history,
                'message': 'Tracking data retrieved'
            }
        return {
            'success': False,
            'message': data.get('message', 'Tracking failed'),
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
        payload = {'awb': awb_number}
        resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
        data = resp.json()

        if resp.status_code == 200 and data.get('status'):
            return {'success': True, 'message': 'Shipment cancelled'}
        return {'success': False, 'message': data.get('message', 'Cancellation failed')}
    except Exception as e:
        print(f'NimbusPost cancel error: {e}')
        return {'success': False, 'message': str(e)}
