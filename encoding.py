import hashlib 

def encode_base62(num: int) -> str:
    """Converts an integer to a Base62 string."""
    BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return BASE62_ALPHABET[0]
    
    arr = []
    while num > 0:
        num, rem = divmod(num, 62)
        arr.append(BASE62_ALPHABET[rem])
    
    # Reverse array because the remainders are calculated from least to most significant
    return "".join(reversed(arr))

def generate_short_code_shake(text: str, length: int=8) -> str:
    """
        shake_256 requires the digest length in bytes
        1 byte = 2 hex characters, so divide desired string length by 2
    """
    byte_length = max(1, length // 2)
    return hashlib.shake_256(text.encode('utf-8')).hexdigest(byte_length)
