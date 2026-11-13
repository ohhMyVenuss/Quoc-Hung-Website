from decimal import Decimal


def format_vnd(amount):

    if amount is None:
        return ""
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    is_negative = amount < 0
    abs_amount = abs(amount)

    s = f"{abs_amount:f}"
    if "." in s:
        int_part_str, frac_part_str = s.split(".", 1)
        frac_part_str = frac_part_str.rstrip("0")
    else:
        int_part_str = s
        frac_part_str = ""

    int_val = int(int_part_str)
    formatted_int = f"{int_val:,}".replace(",", ".")

    sign = "-" if is_negative else ""
    if frac_part_str:
        return f"{sign}{formatted_int},{frac_part_str} đ"
    return f"{sign}{formatted_int} đ"
