from decimal import Decimal
import math
from typing import Dict, List, Optional, Tuple
from django.conf import settings


DEFAULT_QR_TRANSFER_CONFIG = {
    'BANK_NAME': 'Ngân hàng Thử nghiệm Sandbox (TESTBANK)',
    'BANK_BIN': '970400',
    'ACCOUNT_NUMBER': '0982805704',
    'ACCOUNT_HOLDER': 'QUOC HUNG MOBILE TEST',
    'IS_ACTIVE': False,
}


def get_qr_transfer_config() -> dict:
    config = getattr(settings, 'QR_TRANSFER_CONFIG', None)
    if not isinstance(config, dict):
        return {'IS_ACTIVE': False}
    return config


def is_qr_transfer_available() -> bool:
    cfg = get_qr_transfer_config()
    if not cfg.get('IS_ACTIVE', False):
        return False
    required_keys = ['BANK_NAME', 'BANK_BIN', 'ACCOUNT_NUMBER', 'ACCOUNT_HOLDER']
    return all(bool(cfg.get(k) and str(cfg[k]).strip()) for k in required_keys)


def crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for byte in data.encode('utf-8'):
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _format_tlv(tag: str, value: str) -> str:
    val_bytes = value.encode('utf-8')
    return f"{str(tag).zfill(2)}{len(val_bytes):02d}{value}"


def generate_vietqr_payload(
    bank_bin: str,
    account_number: str,
    amount: Decimal | int | str,
    reference: str
) -> str:
    clean_bin = str(bank_bin).strip()
    clean_acc = str(account_number).strip()
    clean_ref = str(reference).strip()

    if isinstance(amount, Decimal):
        if amount == amount.to_integral():
            amt_str = str(int(amount))
        else:
            amt_str = str(amount)
    else:
        amt_str = str(int(Decimal(str(amount))))

    sub00 = _format_tlv('00', 'A000000727')
    sub01_inner = _format_tlv('00', clean_bin) + _format_tlv('01', clean_acc)
    sub01 = _format_tlv('01', sub01_inner)
    sub02 = _format_tlv('02', 'QRIBFTTA')
    tag38 = _format_tlv('38', sub00 + sub01 + sub02)

    tag00 = _format_tlv('00', '01')
    tag01 = _format_tlv('01', '12')
    tag53 = _format_tlv('53', '704')
    tag54 = _format_tlv('54', amt_str)
    tag58 = _format_tlv('58', 'VN')
    tag62 = _format_tlv('62', _format_tlv('08', clean_ref))

    raw_payload = tag00 + tag01 + tag38 + tag53 + tag54 + tag58 + tag62 + '6304'
    checksum = crc16_ccitt(raw_payload)
    return raw_payload + checksum


_QR_VERSION_TABLE_M = [
    (1, 26, 10, [(1, 16)]),
    (2, 44, 16, [(1, 28)]),
    (3, 70, 26, [(1, 44)]),
    (4, 100, 18, [(2, 32)]),
    (5, 134, 24, [(2, 43)]),
    (6, 172, 16, [(4, 27)]),
    (7, 196, 18, [(4, 31)]),
    (8, 242, 22, [(2, 38), (2, 39)]),
    (9, 292, 22, [(3, 36), (2, 37)]),
    (10, 346, 26, [(4, 43), (1, 44)]),
    (11, 404, 30, [(1, 50), (4, 51)]),
    (12, 466, 22, [(6, 36), (2, 37)]),
    (13, 532, 22, [(8, 37), (4, 38)]),
    (14, 581, 24, [(4, 40), (5, 41)]),
]

_ALIGNMENT_LOCATIONS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
    11: [6, 30, 54],
    12: [6, 32, 58],
    13: [6, 34, 62],
    14: [6, 36, 66],
}

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_gx = 1
for _gi in range(255):
    _GF_EXP[_gi] = _gx
    _GF_EXP[_gi + 255] = _gx
    _GF_LOG[_gx] = _gi
    _gx = (_gx << 1) ^ (0x11D if (_gx & 0x80) else 0)


def _gf_mult(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> List[int]:
    poly = [1]
    for i in range(degree):
        root = _GF_EXP[i]
        new_poly = [0] * (len(poly) + 1)
        for j in range(len(poly)):
            new_poly[j] ^= poly[j]
            new_poly[j + 1] ^= _gf_mult(poly[j], root)
        poly = new_poly
    return poly


def _rs_compute_ecc(data: List[int], n_ecc: int) -> List[int]:
    gen = _rs_generator_poly(n_ecc)
    res = list(data) + [0] * n_ecc
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(len(gen)):
                res[i + j] ^= _gf_mult(gen[j], coef)
    return res[len(data):]


def _get_format_info_bits(ec_level_bits: int, mask_pattern: int) -> int:
    data = (ec_level_bits << 3) | mask_pattern
    rem = data << 10
    for i in range(5):
        if rem & (1 << (14 - i)):
            rem ^= (0x537 << (4 - i))
    return ((data << 10) | rem) ^ 0x5412


def _get_version_info_bits(version: int) -> int:
    rem = version << 12
    for i in range(6):
        if rem & (1 << (17 - i)):
            rem ^= (0x1F25 << (5 - i))
    return (version << 12) | rem


def generate_qr_matrix(text: str) -> List[List[bool]]:
    data_bytes = text.encode('utf-8')
    data_len = len(data_bytes)

    selected_ver = None
    for ver_info in _QR_VERSION_TABLE_M:
        ver, total_cw, ec_per_block, blocks = ver_info
        total_data_cw = sum(num_b * b_cw for num_b, b_cw in blocks)
        count_bits = 8 if ver <= 9 else 16
        header_bits = 4 + count_bits
        available_data_bytes = (total_data_cw * 8 - header_bits - 4) // 8
        if available_data_bytes >= data_len:
            selected_ver = ver_info
            break

    if selected_ver is None:
        raise ValueError(f"Dữ liệu QR vượt quá kích thước hỗ trợ ({data_len} bytes).")

    ver, total_cw, ec_per_block, blocks = selected_ver
    total_data_cw = sum(num_b * b_cw for num_b, b_cw in blocks)
    matrix_dim = 17 + 4 * ver

    bitstream = []

    def append_bits(val: int, length: int):
        for i in range(length - 1, -1, -1):
            bitstream.append((val >> i) & 1)

    append_bits(4, 4)
    append_bits(data_len, 8 if ver <= 9 else 16)
    for b in data_bytes:
        append_bits(b, 8)

    total_data_bits = total_data_cw * 8
    pad_needed = min(4, total_data_bits - len(bitstream))
    append_bits(0, pad_needed)

    while len(bitstream) % 8 != 0:
        bitstream.append(0)

    data_codewords = []
    for i in range(0, len(bitstream), 8):
        byte_val = 0
        for bit in bitstream[i:i + 8]:
            byte_val = (byte_val << 1) | bit
        data_codewords.append(byte_val)

    pad_bytes = [0xEC, 0x11]
    pad_idx = 0
    while len(data_codewords) < total_data_cw:
        data_codewords.append(pad_bytes[pad_idx % 2])
        pad_idx += 1

    data_blocks = []
    ec_blocks = []
    curr_offset = 0
    for num_b, b_cw in blocks:
        for _ in range(num_b):
            block_data = data_codewords[curr_offset:curr_offset + b_cw]
            curr_offset += b_cw
            data_blocks.append(block_data)
            ec_blocks.append(_rs_compute_ecc(block_data, ec_per_block))

    final_codewords = []
    max_data_len = max(len(b) for b in data_blocks)
    for col in range(max_data_len):
        for b in data_blocks:
            if col < len(b):
                final_codewords.append(b[col])

    for col in range(ec_per_block):
        for eb in ec_blocks:
            final_codewords.append(eb[col])

    final_bits = []
    for cw in final_codewords:
        for i in range(7, -1, -1):
            final_bits.append((cw >> i) & 1)

    remainder_bits_table = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 12: 0, 13: 3, 14: 3}
    num_remainder = remainder_bits_table.get(ver, 0)
    final_bits.extend([0] * num_remainder)

    grid = [[None for _ in range(matrix_dim)] for _ in range(matrix_dim)]
    is_function = [[False for _ in range(matrix_dim)] for _ in range(matrix_dim)]

    def set_module(r: int, c: int, val: bool, is_fn: bool = True):
        grid[r][c] = val
        if is_fn:
            is_function[r][c] = True

    def place_finder(top_r: int, left_c: int):
        for r in range(7):
            for c in range(7):
                if r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4):
                    set_module(top_r + r, left_c + c, True)
                else:
                    set_module(top_r + r, left_c + c, False)

    place_finder(0, 0)
    place_finder(0, matrix_dim - 7)
    place_finder(matrix_dim - 7, 0)

    def place_separators():
        for i in range(8):
            set_module(7, i, False)
            set_module(i, 7, False)
        for i in range(8):
            set_module(7, matrix_dim - 8 + i, False)
            set_module(i, matrix_dim - 8, False)
        for i in range(8):
            set_module(matrix_dim - 8, i, False)
            set_module(matrix_dim - 8 + i, 7, False)

    place_separators()

    align_coords = _ALIGNMENT_LOCATIONS.get(ver, [])
    for ar in align_coords:
        for ac in align_coords:
            if (ar < 9 and ac < 9) or (ar < 9 and ac > matrix_dim - 10) or (ar > matrix_dim - 10 and ac < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    is_black = (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0))
                    set_module(ar + dr, ac + dc, is_black)

    for i in range(8, matrix_dim - 8):
        set_module(6, i, i % 2 == 0)
        set_module(i, 6, i % 2 == 0)

    set_module(4 * ver + 9, 8, True)

    for c in range(9):
        if c != 6:
            is_function[8][c] = True
    for c in range(matrix_dim - 8, matrix_dim):
        is_function[8][c] = True
    for r in range(9):
        if r != 6:
            is_function[r][8] = True
    for r in range(matrix_dim - 7, matrix_dim):
        is_function[r][8] = True

    if ver >= 7:
        for r in range(matrix_dim - 11, matrix_dim - 8):
            for c in range(6):
                is_function[r][c] = True
        for r in range(6):
            for c in range(matrix_dim - 11, matrix_dim - 8):
                is_function[r][c] = True

    data_bit_idx = 0
    row_dir = -1
    curr_r = matrix_dim - 1
    curr_c = matrix_dim - 1

    while curr_c > 0:
        if curr_c == 6:
            curr_c -= 1
        for col_offset in (0, 1):
            target_c = curr_c - col_offset
            if not is_function[curr_r][target_c]:
                if data_bit_idx < len(final_bits):
                    grid[curr_r][target_c] = (final_bits[data_bit_idx] == 1)
                    data_bit_idx += 1
                else:
                    grid[curr_r][target_c] = False

        curr_r += row_dir
        if curr_r < 0 or curr_r >= matrix_dim:
            row_dir = -row_dir
            curr_r += row_dir
            curr_c -= 2

    mask_functions = [
        lambda r, c: (r + c) % 2 == 0,
        lambda r, c: r % 2 == 0,
        lambda r, c: c % 3 == 0,
        lambda r, c: (r + c) % 3 == 0,
        lambda r, c: (r // 2 + c // 3) % 2 == 0,
        lambda r, c: ((r * c) % 2 + (r * c) % 3) == 0,
        lambda r, c: (((r * c) % 2 + (r * c) % 3) % 2) == 0,
        lambda r, c: (((r + c) % 2 + (r * c) % 3) % 2) == 0,
    ]

    def evaluate_penalty(test_matrix: List[List[bool]]) -> int:
        score = 0
        dim = matrix_dim
        for r in range(dim):
            run_color = test_matrix[r][0]
            run_len = 1
            for c in range(1, dim):
                if test_matrix[r][c] == run_color:
                    run_len += 1
                else:
                    if run_len >= 5:
                        score += 3 + (run_len - 5)
                    run_color = test_matrix[r][c]
                    run_len = 1
            if run_len >= 5:
                score += 3 + (run_len - 5)

        for c in range(dim):
            run_color = test_matrix[0][c]
            run_len = 1
            for r in range(1, dim):
                if test_matrix[r][c] == run_color:
                    run_len += 1
                else:
                    if run_len >= 5:
                        score += 3 + (run_len - 5)
                    run_color = test_matrix[r][c]
                    run_len = 1
            if run_len >= 5:
                score += 3 + (run_len - 5)

        for r in range(dim - 1):
            for c in range(dim - 1):
                val = test_matrix[r][c]
                if val == test_matrix[r + 1][c] == test_matrix[r][c + 1] == test_matrix[r + 1][c + 1]:
                    score += 3

        dark_count = sum(sum(1 for val in row if val) for row in test_matrix)
        total_modules = dim * dim
        pct = (dark_count * 100) // total_modules
        score += (abs(pct - 50) // 5) * 10
        return score

    best_mask = 0
    best_penalty = float('inf')
    best_matrix = None

    for mask_idx, mask_fn in enumerate(mask_functions):
        candidate = [row[:] for row in grid]
        for r in range(matrix_dim):
            for c in range(matrix_dim):
                if not is_function[r][c]:
                    if mask_fn(r, c):
                        candidate[r][c] = not candidate[r][c]

        fmt_bits = _get_format_info_bits(0, mask_idx)
        fmt_coords_tl = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                         (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        for i, (fr, fc) in enumerate(fmt_coords_tl):
            candidate[fr][fc] = bool((fmt_bits >> (14 - i)) & 1)

        fmt_coords_other = [
            (matrix_dim - 1, 8), (matrix_dim - 2, 8), (matrix_dim - 3, 8), (matrix_dim - 4, 8),
            (matrix_dim - 5, 8), (matrix_dim - 6, 8), (matrix_dim - 7, 8),
            (8, matrix_dim - 8), (8, matrix_dim - 7), (8, matrix_dim - 6), (8, matrix_dim - 5),
            (8, matrix_dim - 4), (8, matrix_dim - 3), (8, matrix_dim - 2), (8, matrix_dim - 1),
        ]
        for i, (fr, fc) in enumerate(fmt_coords_other):
            candidate[fr][fc] = bool((fmt_bits >> (14 - i)) & 1)

        if ver >= 7:
            v_bits = _get_version_info_bits(ver)
            bit_k = 0
            for c in range(6):
                for r in range(matrix_dim - 11, matrix_dim - 8):
                    candidate[r][c] = bool((v_bits >> bit_k) & 1)
                    bit_k += 1
            bit_k = 0
            for r in range(6):
                for c in range(matrix_dim - 11, matrix_dim - 8):
                    candidate[r][c] = bool((v_bits >> bit_k) & 1)
                    bit_k += 1

        penalty = evaluate_penalty(candidate)
        if penalty < best_penalty:
            best_penalty = penalty
            best_mask = mask_idx
            best_matrix = candidate

    return best_matrix


def generate_qr_svg(text: str, border: int = 4, size: int = 240) -> str:
    matrix = generate_qr_matrix(text)
    dim = len(matrix)
    total_dim = dim + 2 * border

    path_cmds = []
    for r in range(dim):
        for c in range(dim):
            if matrix[r][c]:
                x = c + border
                y = r + border
                path_cmds.append(f"M{x},{y}h1v1h-1z")

    path_data = "".join(path_cmds)

    svg_content = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_dim} {total_dim}" '
        f'width="{size}" height="{size}" '
        f'shape-rendering="crispEdges" '
        f'class="qhm-qr-svg" '
        f'aria-label="Mã QR thanh toán đơn hàng">'
        f'<rect width="{total_dim}" height="{total_dim}" fill="#ffffff"/>'
        f'<path d="{path_data}" fill="#000000"/>'
        f'</svg>'
    )
    return svg_content
