from slowapi import Limiter

# The carrier limit applies to the whole process, not per caller IP.
limiter = Limiter(key_func=lambda request: "carrier")
