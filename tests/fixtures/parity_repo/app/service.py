from app.models import UserModel
from app.repository import find_user, save_order

class OrderService:
    def create_order(self, user_id, amount):
        uid = user_id
        user = find_user(uid)
        if not user:
            raise ValueError("user not found")
        order = save_order(uid, amount)
        send_email(user.email, "order created")
        return order

    def cancel_order(self, order_id):
        db.execute("UPDATE orders SET cancelled=1 WHERE id=%s", (order_id,))
        return True

def process_payment(card_token, amount):
    import requests
    resp = requests.post("https://api.stripe.com/charges", json={"amount": amount})
    session.add(Payment(amount=amount))
    session.commit()
    return resp.json()
