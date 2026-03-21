class UserModel:
    def __init__(self, user_id, email):
        self.user_id = user_id
        self.email = email

class OrderSchema:
    order_id: str
    amount: float
