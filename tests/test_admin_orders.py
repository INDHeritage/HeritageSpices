"""Admin orders page: paginated list, but totals cover every order."""
from datetime import datetime, timedelta

import app as appmod
from conftest import ADMIN, login


def _orders(n, paid=True, shipping='processing', total=10000, created=None, prefix='HS-P'):
    db = appmod.db
    for i in range(n):
        db.session.add(appmod.SpiceOrder(
            order_number=f'{prefix}{i:04d}', user_email='buyer@example.com', full_name='Buyer', phone='9999999999',
            address='1 Road', city='C', state='S', pincode='441222', subtotal=total, shipping_cost=0,
            total_amount=total, razorpay_order_id=f'order_{prefix}{i}',
            payment_status='paid' if paid else 'pending', shipping_status=shipping,
            created_at=created or datetime.utcnow()))
    db.session.commit()


def test_list_is_paginated_but_totals_cover_everything(client):
    _orders(30, paid=True, total=10000)
    login(client, ADMIN)
    page1 = client.get('/admin/orders').get_data(as_text=True)
    page2 = client.get('/admin/orders?page=2').get_data(as_text=True)
    assert page1.count('font-monospace text-dark') == 25
    assert page2.count('font-monospace text-dark') == 5
    assert 'Showing 25 of 30' in page1
    # totals on both pages describe all 30 orders: 30 x Rs 100 = Rs 3000, all 30 waiting to ship
    assert '₹3000' in page1 and '₹3000' in page2
    assert '<h3 class="fw-bold text-dark mb-0">30</h3>' in page1


def test_delivered_and_pending_shipment_counts(client):
    _orders(2, shipping='delivered', prefix='HS-D')
    _orders(3, shipping='processing', prefix='HS-Q')
    _orders(4, paid=False, shipping='processing', prefix='HS-U')  # unpaid: not "pending shipment"
    login(client, ADMIN)
    html = client.get('/admin/orders').get_data(as_text=True)
    assert '<h3 class="fw-bold text-dark mb-0">9</h3>' in html          # total orders
    assert '<h3 class="fw-bold text-dark mb-0">3</h3>' in html          # pending shipments
    assert '<h3 class="fw-bold text-dark mb-0">2</h3>' in html          # delivered


def test_out_of_range_page_is_harmless(client):
    _orders(3)
    login(client, ADMIN)
    assert client.get('/admin/orders?page=999').status_code == 200


def test_stale_unpaid_orders_are_flagged_once_old_enough(client):
    _orders(1, paid=False, created=datetime.utcnow() - timedelta(hours=2), prefix='HS-S')
    _orders(1, paid=False, created=datetime.utcnow() - timedelta(minutes=5), prefix='HS-N')
    login(client, ADMIN)
    html = client.get('/admin/orders').get_data(as_text=True)
    assert '<strong>1</strong> order unpaid for 30+ minutes' in html


def test_admin_orders_is_admin_only(client):
    login(client, 'someone@example.com')
    assert client.get('/admin/orders').status_code == 403
