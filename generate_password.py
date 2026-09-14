import bcrypt

password = "Mummy123$"

hashed = bcrypt.hashpw(
    password.encode("utf-8")[:72],
    bcrypt.gensalt(rounds=12)
).decode("utf-8")

print(hashed)