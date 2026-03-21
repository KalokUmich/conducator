def find_user(user_identifier):
    return User.query.filter(User.id == user_identifier).first()

def save_order(ref_id, amount):
    db.execute("INSERT INTO orders (user_id, amount) VALUES (%s, %s)", (ref_id, amount))
    return {"id": ref_id, "amount": amount}
