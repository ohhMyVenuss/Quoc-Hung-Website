import re
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

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


def extract_qr_matrix_from_svg(svg_content: str) -> List[List[bool]]:
    if not svg_content or not isinstance(svg_content, str):
        raise ValueError("Nội dung SVG rỗng hoặc không đúng định dạng chuỗi.")

    vb_match = re.search(r'viewBox="0 0 (\d+)\s+(\d+)"', svg_content)
    if not vb_match:
        raise ValueError("Không tìm thấy thuộc tính viewBox hợp lệ trong SVG.")

    total_dim = int(vb_match.group(1))

    points = re.findall(r'M(\d+),(\d+)h1v1h-1z', svg_content)
    if not points:
        raise ValueError("Không tìm thấy lệnh vẽ path điểm ảnh QR trong SVG.")

    coords = [(int(p[0]), int(p[1])) for p in points]
    min_x = min(c[0] for c in coords)
    max_x = max(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    max_y = max(c[1] for c in coords)

    border = min_x
    dim = max_x - min_x + 1

    if (dim - 17) % 4 != 0:
        raise ValueError(f"Kích thước ma trận QR ({dim}) không khớp chuẩn ISO 18004 (4*version + 17).")

    matrix = [[False for _ in range(dim)] for _ in range(dim)]
    for x, y in coords:
        r = y - border
        c = x - border
        if 0 <= r < dim and 0 <= c < dim:
            matrix[r][c] = True

    return matrix


def decode_qr_matrix(matrix: List[List[bool]]) -> str:
    dim = len(matrix)
    if dim < 21 or (dim - 17) % 4 != 0:
        raise ValueError(f"Kích thước ma trận không hợp lệ: {dim}")

    ver = (dim - 17) // 4
    if ver > 14:
        raise ValueError(f"Phiên bản QR {ver} vượt quá phạm vi hỗ trợ (1..14).")

    ver_info = _QR_VERSION_TABLE_M[ver - 1]
    _, total_codewords, ec_per_block, blocks = ver_info

    fmt_coords_tl = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)
    ]
    raw_fmt = 0
    for i, (r, c) in enumerate(fmt_coords_tl):
        if matrix[r][c]:
            raw_fmt |= (1 << (14 - i))

    unmasked_fmt = raw_fmt ^ 0x5412
    mask_pattern = (unmasked_fmt >> 10) & 0x07

    is_function = [[False for _ in range(dim)] for _ in range(dim)]

    def mark_fn(r, c):
        is_function[r][c] = True

    def mark_finder(top_r, left_c):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr = top_r + r
                cc = left_c + c
                if 0 <= rr < dim and 0 <= cc < dim:
                    mark_fn(rr, cc)

    mark_finder(0, 0)
    mark_finder(0, dim - 7)
    mark_finder(dim - 7, 0)

    align_coords = _ALIGNMENT_LOCATIONS.get(ver, [])
    for ar in align_coords:
        for ac in align_coords:
            if (ar < 9 and ac < 9) or (ar < 9 and ac > dim - 10) or (ar > dim - 10 and ac < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    mark_fn(ar + dr, ac + dc)

    for i in range(dim):
        mark_fn(6, i)
        mark_fn(i, 6)

    mark_fn(4 * ver + 9, 8)

    for c in range(9):
        mark_fn(8, c)
    for c in range(dim - 8, dim):
        mark_fn(8, c)
    for r in range(9):
        mark_fn(r, 8)
    for r in range(dim - 7, dim):
        mark_fn(r, 8)

    if ver >= 7:
        for r in range(dim - 11, dim - 8):
            for c in range(6):
                mark_fn(r, c)
        for r in range(6):
            for c in range(dim - 11, dim - 8):
                mark_fn(r, c)

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
    mask_fn = mask_functions[mask_pattern]

    unmasked_grid = [[False for _ in range(dim)] for _ in range(dim)]
    for r in range(dim):
        for c in range(dim):
            if not is_function[r][c]:
                val = matrix[r][c]
                if mask_fn(r, c):
                    val = not val
                unmasked_grid[r][c] = val

    raw_bits = []
    curr_r = dim - 1
    curr_c = dim - 1
    row_dir = -1

    while curr_c > 0:
        if curr_c == 6:
            curr_c -= 1
        for col_offset in (0, 1):
            target_c = curr_c - col_offset
            if not is_function[curr_r][target_c]:
                raw_bits.append(1 if unmasked_grid[curr_r][target_c] else 0)
        curr_r += row_dir
        if curr_r < 0 or curr_r >= dim:
            row_dir = -row_dir
            curr_r += row_dir
            curr_c -= 2

    num_total_bits = total_codewords * 8
    codewords = []
    for i in range(0, num_total_bits, 8):
        byte_val = 0
        for bit_i in range(8):
            if i + bit_i < len(raw_bits):
                byte_val = (byte_val << 1) | raw_bits[i + bit_i]
        codewords.append(byte_val)

    data_block_defs = []
    for num_b, b_cw in blocks:
        for _ in range(num_b):
            data_block_defs.append(b_cw)

    num_data_blocks = len(data_block_defs)
    max_data_len = max(data_block_defs)
    total_data_codewords = sum(data_block_defs)

    data_blocks = [[] for _ in range(num_data_blocks)]
    cw_idx = 0

    for col in range(max_data_len):
        for b_idx in range(num_data_blocks):
            if col < data_block_defs[b_idx]:
                if cw_idx < len(codewords):
                    data_blocks[b_idx].append(codewords[cw_idx])
                    cw_idx += 1

    data_codewords = []
    for b in data_blocks:
        data_codewords.extend(b)

    data_bitstream = []
    for cw in data_codewords:
        for i in range(7, -1, -1):
            data_bitstream.append((cw >> i) & 1)

    def read_bits(start: int, count: int) -> Tuple[int, int]:
        val = 0
        for i in range(count):
            if start + i < len(data_bitstream):
                val = (val << 1) | data_bitstream[start + i]
        return val, start + count

    bit_ptr = 0
    mode, bit_ptr = read_bits(bit_ptr, 4)
    if mode != 4:
        raise ValueError(f"Chế độ mã hóa không phải Byte Mode (mode={mode}).")

    count_bits = 8 if ver <= 9 else 16
    char_count, bit_ptr = read_bits(bit_ptr, count_bits)

    out_bytes = bytearray()
    for _ in range(char_count):
        char_val, bit_ptr = read_bits(bit_ptr, 8)
        out_bytes.append(char_val)

    return out_bytes.decode('utf-8')


def parse_emvco_tlv(payload: str) -> Dict[str, any]:
    if not payload or len(payload) < 8:
        raise ValueError("Chuỗi payload QR quá ngắn để chứa dữ liệu TLV hợp lệ.")

    def parse_tlv_string(s: str) -> Dict[str, str]:
        tags = {}
        idx = 0
        s_bytes = s.encode('utf-8')
        while idx < len(s_bytes):
            tag = s_bytes[idx:idx+2].decode('utf-8', errors='ignore')
            idx += 2
            if idx + 2 > len(s_bytes):
                break
            length_str = s_bytes[idx:idx+2].decode('utf-8', errors='ignore')
            idx += 2
            if not length_str.isdigit():
                break
            length = int(length_str)
            val = s_bytes[idx:idx+length].decode('utf-8', errors='ignore')
            idx += length
            tags[tag] = val
        return tags

    top_tags = parse_tlv_string(payload)

    crc_tag = top_tags.get('63')
    is_crc_valid = False
    if crc_tag and len(crc_tag) == 4:
        crc_idx = payload.rfind('6304')
        if crc_idx != -1:
            data_to_check = payload[:crc_idx + 4]
            from payments.qr import crc16_ccitt
            expected_crc = crc16_ccitt(data_to_check)
            is_crc_valid = (expected_crc.upper() == crc_tag.upper())

    sub38 = {}
    bank_bin = ''
    account_number = ''
    if '38' in top_tags:
        sub38 = parse_tlv_string(top_tags['38'])
        if '01' in sub38:
            nested_sub01 = parse_tlv_string(sub38['01'])
            bank_bin = nested_sub01.get('00', '')
            account_number = nested_sub01.get('01', '')

    reference = ''
    if '62' in top_tags:
        sub62 = parse_tlv_string(top_tags['62'])
        reference = sub62.get('08', '')

    amount_str = top_tags.get('54', '')

    return {
        'raw_payload': payload,
        'bank_bin': bank_bin,
        'account_number': account_number,
        'amount': amount_str,
        'reference': reference,
        'crc': crc_tag,
        'is_crc_valid': is_crc_valid,
        'top_tags': top_tags,
    }


def decode_vietqr_svg(svg_content: str) -> Dict[str, any]:
    matrix = extract_qr_matrix_from_svg(svg_content)
    payload = decode_qr_matrix(matrix)
    parsed = parse_emvco_tlv(payload)
    parsed['matrix_dim'] = len(matrix)
    return parsed

