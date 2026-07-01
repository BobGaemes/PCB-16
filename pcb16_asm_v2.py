#!/usr/bin/env python3
"""
PCB-16 PopCornBoard — Assembler v2
====================================================
En mnemonic per instruktionsfamilj (ADC, LDR, JMP, STR, BNE, ...) — operandens
FORM avgör automatiskt vilken opcode-variant (R/#/a/(a,a)/(a,b)/r) som väljs.

OPERANDSYNTAX
-------------
  A-H            register
  #värde         immediate          (DATA-fält, 16-bit)
  $addr          absolut adress
  $addr,A        absolut adress indexerad med register A
  $addr,B        absolut adress indexerad med register B
  -$32 / 50      signerat relativt värde (bara för branch/JMPREL — inget '#' eller '$addr,' format)
  label          (v2: symbol, slås upp i symboltabellen — se .org/labels nedan)

TAL-PREFIX (gäller efter #, $ eller direkt för relativa/labels)
  (ingen prefix) decimal       t.ex. 123, -5
  $              hexadecimal   t.ex. $1A2B
  %              binär         t.ex. %1010
  "x"            ASCII-tecken  t.ex. "A" -> 65

DIREKTIV
  .org $ADDR     sätter nuvarande skrivadress (för kod ELLER för en enstaka .word)
  .word VÄRDE    skriver ett rått 16-bitars ord på nuvarande adress, ökar adressen med 1
  label:         definierar en symbol = nuvarande adress (kan användas som operand)

Exempel:
    .org $8000
    start:
        LDR A,#5
        LDR B,#3
        ADC A,B,C
    loop:
        INC A
        JMP loop          ; JMP med en label -> absolut hopp till label:s adress
        BNE -5            ; relativt hopp, signerat värde direkt (inget $/# prefix)

    .org $FFFF
    .word start           ; reset-vektor pekar på start
"""

import re
import sys

# ── Register-tabell ────────────────────────────────────────────────────────
REG = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7}


class AsmError(Exception):
    def __init__(self, msg, line_no=None, line_text=None):
        self.line_no = line_no
        self.line_text = line_text
        full = msg
        if line_no is not None:
            full = f"Rad {line_no}: {msg}"
            if line_text:
                full += f"  (\"{line_text.strip()}\")"
        super().__init__(full)


# ── Låg-nivå packningsfunktioner (oförändrade, verifierade mot mikrokoden) ──
def pack_simple(opcode):
    return [opcode & 0x7F]


def pack_regx(opcode, regx):
    w = (opcode & 0x7F) | ((regx & 0b111) << 7)
    return [w & 0xFFFF]


def pack_3reg(opcode, regx, regy, regz):
    w = (opcode & 0x7F) | ((regx & 0b111) << 7) | ((regy & 0b111) << 10) | ((regz & 0b111) << 13)
    return [w & 0xFFFF]


def pack_regx_baked_y1(opcode, regx):
    w = (opcode & 0x7F) | ((regx & 0b111) << 7) | (0b001 << 10)
    return [w & 0xFFFF]


def pack_baked_x1(opcode):
    w = (opcode & 0x7F) | (0b001 << 7)
    return [w & 0xFFFF]


def pack_regx_value16(opcode, regx, value16):
    full = (opcode & 0x7F) | ((regx & 0b111) << 7) | ((value16 & 0xFFFF) << 10)
    return [full & 0xFFFF, (full >> 16) & 0xFFFF]


def pack_value16(opcode, value16):
    full = (opcode & 0x7F) | ((value16 & 0xFFFF) << 7)
    return [full & 0xFFFF, (full >> 16) & 0xFFFF]


def pack_regx_value16_extra(opcode, regx, value16, extra3):
    full = (opcode & 0x7F) | ((regx & 0b111) << 7) | ((value16 & 0xFFFF) << 10)
    word1 = full & 0xFFFF
    word2 = (full >> 16) & 0xFFFF
    word2 |= (extra3 & 0b111) << 10
    return [word1, word2 & 0xFFFF]


def pack_value16_extra(opcode, value16, extra3):
    full = (opcode & 0x7F) | ((value16 & 0xFFFF) << 7)
    word1 = full & 0xFFFF
    word2 = (full >> 16) & 0xFFFF
    word2 |= (extra3 & 0b111) << 7
    return [word1, word2 & 0xFFFF]


# ── Sanity checks mot tidigare bekräftade exempel ───────────────────────────
assert pack_regx_value16(0x68, REG['A'], 5) == [0x1468, 0x0000]
assert pack_regx_value16(0x68, REG['B'], 3) == [0x0CE8, 0x0000]
assert pack_3reg(0x04, REG['A'], REG['B'], REG['C']) == [0x4404]
assert pack_baked_x1(0x7A) == [0x00FA]
assert pack_regx_baked_y1(0x2B, REG['A']) == [0x042B]
assert pack_regx(0x78, 0) == [0x0078]


# ── Operand-klassificering ──────────────────────────────────────────────────
# En operand klassas som ett av: REG, IMM, ABS, ABS_IDX, RELATIVE, SYMBOL
# (SYMBOL upplöses till IMM/ABS/RELATIVE i pass 2 när symboltabellen är klar.)

IDX_A = REG['A']
IDX_B = REG['B']

ABS_IDX_RE = re.compile(r'^(.*?)\s*,\s*([ABab])\s*$')


def parse_number(tok):
    """Tolkar ett tal enligt prefix-konventionen:
      $hex   %bin   "c" (ASCII)   annars decimal (inkl. negativa: -5, -$1A)."""
    t = tok.strip()
    neg = False
    if t.startswith('-'):
        neg = True
        t = t[1:].strip()
    if t.startswith('"') and t.endswith('"') and len(t) == 3:
        val = ord(t[1])
    elif t.startswith('$'):
        val = int(t[1:], 16)
    elif t.startswith('%'):
        val = int(t[1:], 2)
    else:
        val = int(t, 10)
    return -val if neg else val


def is_register(tok):
    return tok.strip().upper() in REG


def classify_operand(tok):
    """Returnerar en av: ('REG', regnum) | ('IMM', value) | ('ABS', value_or_symbol)
    | ('ABS_IDX', value_or_symbol, idx_reg) | ('REL', value_or_symbol) | ('SYM', name)
    Symboler (bokstäver/siffror som inte är ett rent tal eller register) lämnas
    olösta som ('SYM', namn) och löses i pass 2 mot symboltabellen — då vet vi
    om en symbol ska tolkas som absolut adress eller (för branches) som relativt
    värde, vilket bestäms av VILKEN instruktion symbolen användes i (se encoders)."""
    t = tok.strip()

    if is_register(t):
        return ('REG', REG[t.upper()])

    if t.startswith('#'):
        inner = t[1:]
        if _looks_numeric(inner):
            return ('IMM', parse_number(inner))
        return ('IMM_SYM', inner)

    m = ABS_IDX_RE.match(t)
    if m:
        addr_str, idx_letter = m.group(1).strip(), m.group(2).upper()
        idx = IDX_A if idx_letter == 'A' else IDX_B
        if addr_str.startswith('$') or _looks_numeric(addr_str):
            return ('ABS_IDX', parse_number(addr_str), idx)
        return ('ABS_IDX_SYM', addr_str, idx)

    if t.startswith('$') or _looks_numeric(t):
        return ('ABS_OR_REL', parse_number(t))  # disambigueras av encoder (vet om instr är branch/rel)

    if t.startswith('"'):
        return ('ABS_OR_REL', parse_number(t))

    # Annars: ren symbol (label), används som adress ELLER relativt värde beroende
    # på vilken instruktion den förekommer i — encoder avgör.
    return ('SYM', t)


def _looks_numeric(s):
    s = s.strip()
    if not s:
        return False
    if s[0] == '-':
        s = s[1:]
    if s.startswith('$') or s.startswith('%'):
        return True
    if s.startswith('"') and s.endswith('"') and len(s) == 3:
        return True
    return s.isdigit()


class AsmContext:
    """Håller symboltabell + olösta referenser mellan pass 1 och pass 2."""
    def __init__(self):
        self.symbols = {}  # namn -> adress (int)

    def resolve(self, name, line_no=None, line_text=None):
        if name not in self.symbols:
            raise AsmError(f"okänd symbol '{name}'", line_no, line_text)
        return self.symbols[name]


# ── Encoder-funktioner som tar klassificerade operander ─────────────────────
# Varje familj-encoder får en lista av råa operand-STRÄNGAR (inte klassificerade
# än) plus ctx (symboltabell) + (line_no, line_text) för felmeddelanden, och
# returnerar listan av ord. Den klassificerar operanderna själv via classify_operand
# och löser symboler via ctx.resolve omedelbart (kräver att alla labels är kända —
# se two-pass assembly i assemble()).

def _need(operands, n, line_no, line_text):
    if len(operands) != n:
        raise AsmError(f"förväntade {n} operand(er), fick {len(operands)}: {operands}", line_no, line_text)


def _resolve_value(kind, val, ctx, line_no, line_text):
    """För ABS_OR_REL/SYM-klasser: om symbol, slå upp i ctx. Returnerar rått heltal."""
    if kind in ('SYM', 'IMM_SYM', 'ABS_IDX_SYM'):
        return ctx.resolve(val, line_no, line_text)
    return val


def enc_alu_family(mnem, base_opcode, has_writeback=True):
    """ADC/SBC/OR/AND/XOR (writeback) eller CMP/BIT (ingen writeback).
    Former:
      MNEM regX,regY,regZ        -> R-form (3 reg)         [bara om writeback]
      MNEM regX,regY             -> R-form utan Z=writeback till X, ELLER CMP/BIT 2-reg
      MNEM regX,#imm             -> immediate
      MNEM regX,$addr            -> absolut
      MNEM regX,$addr,A/B        -> indexerad
    """
    r_op, imm_op, abs_op, ia_op, ib_op = (base_opcode + i for i in range(5))

    def encode(operands, ctx, line_no, line_text):
        if len(operands) == 3 and has_writeback:
            # MNEM X,Y,Z (alla register)
            kinds = [classify_operand(o) for o in operands]
            if not all(k[0] == 'REG' for k in kinds):
                raise AsmError(f"{mnem} med 3 operander förväntar 3 register", line_no, line_text)
            x, y, z = (k[1] for k in kinds)
            return pack_3reg(r_op, x, y, z)

        if len(operands) == 2:
            kx = classify_operand(operands[0])
            if kx[0] != 'REG':
                raise AsmError(f"{mnem}: första operanden måste vara ett register", line_no, line_text)
            x = kx[1]
            ky = classify_operand(operands[1])

            if ky[0] == 'REG':
                # 2-register form: ADC X,Y -> Z=X (writeback till X) ELLER CMP X,Y (ingen Z)
                y = ky[1]
                if has_writeback:
                    return pack_3reg(r_op, x, y, x)  # writeback till X själv
                return pack_3reg(r_op, x, y, 0)

            if ky[0] in ('IMM', 'IMM_SYM'):
                val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
                return pack_regx_value16(imm_op, x, val)

            if ky[0] in ('ABS_OR_REL', 'SYM'):
                val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
                return pack_regx_value16(abs_op, x, val)

            if ky[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
                val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
                idx = ky[2]
                op = ia_op if idx == IDX_A else ib_op
                return pack_regx_value16_extra(op, x, val, idx)

            raise AsmError(f"{mnem}: ogiltig andra operand '{operands[1]}'", line_no, line_text)

        raise AsmError(f"{mnem}: fel antal operander ({len(operands)})", line_no, line_text)

    return encode


def enc_unary_reg(opcode):
    """NEG/LSR/LSL/ROR/ROL: ett register, läser+skriver samma."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] != 'REG':
            raise AsmError("förväntade ett register", line_no, line_text)
        return pack_regx(opcode, k[1])
    return encode


def enc_incdec(opcode_r, opcode_abs, opcode_ia, opcode_ib):
    """INC/DEC: stödjer alla fyra adresseringslägen.
      INC regX            -> R-form: regX(7-9) + bakad konstant 1 i regY(10-12)
      INC $addr           -> absolut: ADDRESS(16-bit) + bakad konstant 1 i OP_EXTRA (word2 bit7-9)
      INC $addr,A / ,B    -> indexerad: ADDRESS(16-bit) + RIKTIGT indexregister i OP_EXTRA
    OBS: 'a'-formerna (absolut/indexerad) jobbar direkt på minnet, ingen regX
    inblandad alls -- precis som JMP a saknar regX."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])

        if k[0] == 'REG':
            return pack_regx_baked_y1(opcode_r, k[1])

        if k[0] in ('ABS_OR_REL', 'SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            return pack_value16_extra(opcode_abs, val, 0b001)  # bakad konstant "1"

        if k[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            idx = k[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_value16_extra(op, val, idx)

        raise AsmError(f"INC/DEC: ogiltig operand '{operands[0]}'", line_no, line_text)
    return encode


def enc_none(opcode):
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 0, line_no, line_text)
        return pack_simple(opcode)
    return encode


def enc_baked1_only(opcode):
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 0, line_no, line_text)
        return pack_baked_x1(opcode)
    return encode


def enc_ldr(opcode_imm, opcode_abs, opcode_ia, opcode_ib):
    """LDR regX, <#imm | $addr | $addr,A | $addr,B>"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 2, line_no, line_text)
        kx = classify_operand(operands[0])
        if kx[0] != 'REG':
            raise AsmError("LDR: första operanden måste vara ett register", line_no, line_text)
        x = kx[1]
        ky = classify_operand(operands[1])
        if ky[0] in ('IMM', 'IMM_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_imm, x, val)
        if ky[0] in ('ABS_OR_REL', 'SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_abs, x, val)
        if ky[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            idx = ky[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_regx_value16_extra(op, x, val, idx)
        raise AsmError(f"LDR: ogiltig andra operand '{operands[1]}'", line_no, line_text)
    return encode


def enc_str(opcode_abs, opcode_ia, opcode_ib):
    """STR regX, <$addr | $addr,A | $addr,B>  (ingen immediate-variant finns)"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 2, line_no, line_text)
        kx = classify_operand(operands[0])
        if kx[0] != 'REG':
            raise AsmError("STR: första operanden måste vara ett register", line_no, line_text)
        x = kx[1]
        ky = classify_operand(operands[1])
        if ky[0] in ('ABS_OR_REL', 'SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_abs, x, val)
        if ky[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            idx = ky[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_regx_value16_extra(op, x, val, idx)
        raise AsmError(f"STR: ogiltig andra operand '{operands[1]}' (STR har ingen immediate-form)", line_no, line_text)
    return encode


def enc_branch(opcode_rel, opcode_abs):
    """BNE/BEQ/BCC/BCS/BPL/BMI/BVC/BVS:
       MNEM -$32 / 5        -> relativ (signerat tal direkt, inget #/$-prefix krav -- $ tillåts också)
       MNEM $addr           -> absolut
       MNEM label           -> SYM, tolkas som RELATIVT värde om det är ett rent tal-uttryck
                               annars (vanligen) som en adress -- se regel nedan.
    REGEL för att skilja relativ vs absolut när operanden är ett rent tal eller en symbol:
      - Om operanden börjar med '$' -> alltid ABSOLUT (skriv $-adress explicit för absolut hopp)
      - Om operanden är ett vanligt signerat decimaltal (ex -5, 5) -> RELATIVT
      - Om operanden är en SYMBOL (label) -> ABSOLUT (vanligast: hoppa till en namngiven plats)
        (Vill man hoppa relativt till en label, räkna ut differensen manuellt för nu —
         v3-hook: stöd för automatisk relativ-till-label-beräkning kan läggas till här.)
    """
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        tok = operands[0].strip()

        if k[0] == 'ABS_OR_REL':
            if tok.startswith('$'):
                return pack_value16(opcode_abs, k[1])
            else:
                return pack_value16(opcode_rel, k[1] & 0xFFFF)
        if k[0] == 'SYM':
            val = ctx.resolve(k[1], line_no, line_text)
            return pack_value16(opcode_abs, val)
        raise AsmError(f"branch: ogiltig operand '{operands[0]}'", line_no, line_text)
    return encode


def enc_jmp(opcode_reg, opcode_rel, opcode_abs, opcode_ia, opcode_ib):
    """JMP <regX | -relativt_värde | $addr | $addr,A | $addr,B | label>"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        tok = operands[0].strip()

        if k[0] == 'REG':
            return pack_regx(opcode_reg, k[1])
        if k[0] == 'ABS_OR_REL':
            if tok.startswith('$'):
                return pack_value16(opcode_abs, k[1])
            else:
                return pack_value16(opcode_rel, k[1] & 0xFFFF)
        if k[0] == 'SYM':
            val = ctx.resolve(k[1], line_no, line_text)
            return pack_value16(opcode_abs, val)
        if k[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            idx = k[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_value16_extra(op, val, idx)
        raise AsmError(f"JMP: ogiltig operand '{operands[0]}'", line_no, line_text)
    return encode


def enc_addr_simple(opcode):
    """JSR/BRK/PHR#: tar bara $addr eller label (ABS_OR_REL/SYM), inget regX."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] in ('ABS_OR_REL', 'SYM', 'IMM', 'IMM_SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            return pack_value16(opcode, val)
        raise AsmError(f"förväntade adress/värde, fick '{operands[0]}'", line_no, line_text)
    return encode


def enc_regx_imm_only(opcode):
    """PIE port: tar ett rått portnummer (0-3 -- bara 4 portar finns fysiskt,
    även om fältet i sig är 3 bitar brett / kan adressera 0-7). Tillåter
    även '#imm' eller rått tal."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] in ('IMM', 'IMM_SYM', 'ABS_OR_REL', 'SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            if not (0 <= val <= 3):
                raise AsmError(f"portnummer måste vara 0-3 (bara 4 portar finns), fick {val}", line_no, line_text)
            return pack_regx(opcode, val)
        raise AsmError(f"förväntade portnummer, fick '{operands[0]}'", line_no, line_text)
    return encode


def enc_phr(opcode_reg, opcode_imm):
    """PHR <regX | #imm>"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] == 'REG':
            return pack_regx(opcode_reg, k[1])
        if k[0] in ('IMM', 'IMM_SYM', 'ABS_OR_REL', 'SYM'):
            val = _resolve_value(k[0], k[1], ctx, line_no, line_text)
            return pack_value16(opcode_imm, val)
        raise AsmError(f"PHR: ogiltig operand '{operands[0]}'", line_no, line_text)
    return encode

def enc_psd(opcode_reg, opcode_imm, opcode_abs, opcode_ia, opcode_ib):
    """PSD port, <regX | #imm | $addr | $addr,A | $addr,B>"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 2, line_no, line_text)
        kp = classify_operand(operands[0])
        if kp[0] not in ('IMM', 'IMM_SYM', 'ABS_OR_REL', 'SYM'):
            raise AsmError("PSD: första operanden måste vara ett portnummer (0-3)", line_no, line_text)
        port = _resolve_value(kp[0], kp[1], ctx, line_no, line_text)
        if not (0 <= port <= 3):
            raise AsmError(f"PSD: portnummer måste vara 0-3, fick {port}", line_no, line_text)
        
        ky = classify_operand(operands[1])
        if ky[0] == 'REG':
            return pack_3reg(opcode_reg, port, ky[1], 0)
        if ky[0] in ('IMM', 'IMM_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_imm, port, val)
        if ky[0] in ('ABS_OR_REL', 'SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_abs, port, val)
        if ky[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            idx = ky[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_regx_value16_extra(op, port, val, idx)
        raise AsmError(f"PSD: ogiltig andra operand '{operands[1]}'", line_no, line_text)
    return encode


def enc_prd(opcode_reg, opcode_abs, opcode_ia, opcode_ib):
    """PRD port, <regX | $addr | $addr,A | $addr,B>"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 2, line_no, line_text)
        kp = classify_operand(operands[0])
        if kp[0] not in ('IMM', 'IMM_SYM', 'ABS_OR_REL', 'SYM'):
            raise AsmError("PRD: första operanden måste vara ett portnummer (0-3)", line_no, line_text)
        port = _resolve_value(kp[0], kp[1], ctx, line_no, line_text)
        if not (0 <= port <= 3):
            raise AsmError(f"PRD: portnummer måste vara 0-3, fick {port}", line_no, line_text)
        
        ky = classify_operand(operands[1])
        if ky[0] == 'REG':
            return pack_3reg(opcode_reg, port, ky[1], 0)
        if ky[0] in ('ABS_OR_REL', 'SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            return pack_regx_value16(opcode_abs, port, val)
        if ky[0] in ('ABS_IDX', 'ABS_IDX_SYM'):
            val = _resolve_value(ky[0], ky[1], ctx, line_no, line_text)
            idx = ky[2]
            op = opcode_ia if idx == IDX_A else opcode_ib
            return pack_regx_value16_extra(op, port, val, idx)
        raise AsmError(f"PRD: ogiltig andra operand '{operands[1]}'", line_no, line_text)
    return encode

# ── Mnemonic-tabell ──────────────────────────────────────────────────────────
OPCODE_TABLE = {}


def register(mnemonic, encode_fn):
    if mnemonic in OPCODE_TABLE:
        raise ValueError(f"Mnemonic '{mnemonic}' redan registrerad!")
    OPCODE_TABLE[mnemonic] = encode_fn


register('ADC', enc_alu_family('ADC', 0x04, has_writeback=True))
register('SBC', enc_alu_family('SBC', 0x09, has_writeback=True))
register('OR',  enc_alu_family('OR',  0x12, has_writeback=True))
register('AND', enc_alu_family('AND', 0x17, has_writeback=True))
register('XOR', enc_alu_family('XOR', 0x1C, has_writeback=True))
register('CMP', enc_alu_family('CMP', 0x21, has_writeback=False))
register('BIT', enc_alu_family('BIT', 0x26, has_writeback=False))

register('NEG', enc_unary_reg(0x0E))
register('INC', enc_incdec(0x2B, 0x2C, 0x2D, 0x2E))
register('DEC', enc_incdec(0x2F, 0x30, 0x31, 0x32))
register('LSR', enc_unary_reg(0x33))
register('LSL', enc_unary_reg(0x37))
register('ROR', enc_unary_reg(0x3B))
register('ROL', enc_unary_reg(0x3F))

register('PHR', enc_phr(0x43, 0x44))
register('PLR', enc_unary_reg(0x48))
register('PHP', enc_none(0x4C))
register('PLP', enc_none(0x4D))

register('BNE', enc_branch(0x50, 0x51))
register('BEQ', enc_branch(0x52, 0x53))
register('BCC', enc_branch(0x54, 0x55))
register('BCS', enc_branch(0x56, 0x57))
register('BPL', enc_branch(0x58, 0x59))
register('BMI', enc_branch(0x5A, 0x5B))
register('BVC', enc_branch(0x5C, 0x5D))
register('BVS', enc_branch(0x5E, 0x5F))

register('JMP', enc_jmp(0x60, 0x61, 0x62, 0x63, 0x64))
register('JSR', enc_addr_simple(0x65))
register('RTS', enc_none(0x66))
register('RTI', enc_none(0x67))

register('LDR', enc_ldr(0x68, 0x69, 0x6A, 0x6B))
register('STR', enc_str(0x6C, 0x6D, 0x6E))

register('PSD', enc_psd(0x6F, 0x70, 0x71, 0x72, 0x73))
register('PRD', enc_prd(0x74, 0x75, 0x76, 0x77))
register('PIE', enc_regx_imm_only(0x78))

register('CLI', enc_none(0x79))
register('SEI', enc_baked1_only(0x7A))
register('CLC', enc_none(0x7B))
register('SEC', enc_baked1_only(0x7C))

register('CPC', enc_unary_reg(0x7D))
register('CSP', enc_unary_reg(0x7E))
register('CPR', enc_unary_reg(0x7F))

register('RST', enc_none(0x00))
register('HLT', enc_none(0x01))
register('BRK', enc_addr_simple(0x02))
register('WAI', enc_none(0x03))


# ── Pseudoinstruktioner ──────────────────────────────────────────────────────
# Dessa lägger INTE till några nya opcodes/mikrokod -- de kodar bara om till
# en redan existerande instruktion/adresseringsform. Ren assembler-bekvämlighet.

def enc_nop():
    """NOP  ->  AND A,A,A  (ingen förändring av registerinnehåll; sätter
    flaggor baserat på A -- ett bakat register krävs eftersom NOP saknar
    operand i tabellen)."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 0, line_no, line_text)
        return pack_3reg(0x17, REG['A'], REG['A'], REG['A'])  # AND R-op
    return encode


def enc_cpy():
    """CPY regX, regY  ->  AND X,X,Y   (X → Y, X oförändrat)"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 2, line_no, line_text)
        kx = classify_operand(operands[0])
        ky = classify_operand(operands[1])
        if kx[0] != 'REG' or ky[0] != 'REG':
            raise AsmError("CPY: båda operander måste vara register", line_no, line_text)
        return pack_3reg(0x17, kx[1], kx[1], ky[1])  # AND R-op
    return encode


def enc_shl():
    """SHL regX  ->  ADC X,X,X   (X + X = X<<1 → X)"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] != 'REG':
            raise AsmError("SHL: förväntade ett register", line_no, line_text)
        return pack_3reg(0x04, k[1], k[1], k[1])  # ADC R-op
    return encode


def enc_not():
    """NOT regX  ->  XOR X,$FFFF   (bitwise NOT, skriver tillbaka till X).
    OBS: tabellens 'XOR X #1 X' flippar bara bit 0 -- $FFFF används här för
    att matcha den avsedda semantiken (Bitwise NOT / !X -> X)."""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] != 'REG':
            raise AsmError("NOT: förväntade ett register", line_no, line_text)
        return pack_regx_value16(0x1D, k[1], 0xFFFF)  # XOR imm-op
    return encode


def enc_clr():
    """CLR regX  ->  XOR X,X,X   (0 → X)"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] != 'REG':
            raise AsmError("CLR: förväntade ett register", line_no, line_text)
        return pack_3reg(0x1C, k[1], k[1], k[1])  # XOR R-op
    return encode


def enc_tst():
    """TST regX  ->  CMP X,#0"""
    def encode(operands, ctx, line_no, line_text):
        _need(operands, 1, line_no, line_text)
        k = classify_operand(operands[0])
        if k[0] != 'REG':
            raise AsmError("TST: förväntade ett register", line_no, line_text)
        return pack_regx_value16(0x22, k[1], 0)  # CMP imm-op
    return encode


register('NOP',  enc_nop())
register('CPY',  enc_cpy())
register('SHL',  enc_shl())
register('NOT',  enc_not())
register('CLR',  enc_clr())
register('CALL', enc_addr_simple(0x65))   # alias för JSR
register('RET',  enc_none(0x66))          # alias för RTS
register('TST',  enc_tst())
register('BZC',  enc_branch(0x50, 0x51))  # alias för BNE (Z=0 = "zero clear")
register('BZS',  enc_branch(0x52, 0x53))  # alias för BEQ (Z=1 = "zero set")


# ── Radparser ────────────────────────────────────────────────────────────────
COMMENT_RE = re.compile(r';.*$')
LABEL_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$')
CONST_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$')
DIRECTIVE_ORG_RE = re.compile(r'^\.org\s+(.+)$', re.IGNORECASE)
DIRECTIVE_WORD_RE = re.compile(r'^\.word\s+(.+)$', re.IGNORECASE)
INSTR_RE = re.compile(r'^([A-Za-z]+)\s*(.*)$')


def strip_comment(line):
    return COMMENT_RE.sub('', line)


def split_operands(rest):
    """Delar på kommatecken som INTE ligger inom parenteser. Slår sedan ihop
    ett eventuellt avslutande ',A' eller ',B'-index-suffix tillbaka in i FÖREGÅENDE
    operand, eftersom '$addr,A' ska tolkas som EN operand (indexerad adress),
    inte två separata. Sammanslagning sker bara om:
      - sista delen är EXAKT 'A' eller 'B' (skiftläges-oberoende), OCH
      - näst sista delen ser ut som en adress ($-prefix eller rent numerisk
        eller en symbol) -- INTE ett enda registernamn, för att skilja
        'STR A,$3450,A' (indexerad adress) från t.ex. 'ADC A,B,A' (tre
        riktiga register, där sista 'A' INTE ska slås ihop med 'B')."""
    rest = rest.strip()
    if not rest:
        return []
    parts, depth, current = [], 0, []
    for ch in rest:
        if ch == '(':
            depth += 1; current.append(ch)
        elif ch == ')':
            depth -= 1; current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current).strip()); current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current).strip())

    if len(parts) >= 2 and parts[-1].upper() in ('A', 'B'):
        prev = parts[-2]
        prev_is_plain_register = prev.upper() in REG and len(prev) == 1
        if not prev_is_plain_register:
            merged = f"{prev},{parts[-1]}"
            parts = parts[:-2] + [merged]

    return parts


class Item:
    """Ett tolkat men ev. inte färdig-encodat element från pass 1."""
    __slots__ = ('kind', 'addr', 'line_no', 'line_text', 'mnemonic', 'operands', 'value')

    def __init__(self, kind, addr, line_no, line_text, mnemonic=None, operands=None, value=None):
        self.kind = kind  # 'instr' | 'word'
        self.addr = addr
        self.line_no = line_no
        self.line_text = line_text
        self.mnemonic = mnemonic
        self.operands = operands
        self.value = value


def assemble(source_text):
    """Two-pass assembler:
      Pass 1: gå igenom raderna, hantera .org/labels, samla items (instr/word)
              med sina FAKTISKA adresser, bygg symboltabell. Vi vet inte ännu
              hur många ord varje instruktion blir (kräver att vi encodear),
              så vi encodear redan i pass 1 -- MEN framåtreferenser till labels
              (en JMP till en label som definieras senare i filen) kan då inte
              lösas än. Lösning: vi gör en första snabb "längd-bara" pass för
              att räkna ut adresser, sen en andra pass som faktiskt encodear
              med fullständig symboltabell.

    Returnerar: dict {adress: ord} (sparse, så .org-hopp/gap hanteras naturligt).
    """
    lines = source_text.splitlines()
    ctx = AsmContext()

    # ── Pass 1a: räkna ut adresser och samla rå (ej encodade) rader ─────────
    raw_items = []  # (kind, addr, line_no, line_text, mnemonic_or_None, operands_or_value)
    addr = 0
    org_seen = False

    for line_no, raw_line in enumerate(lines, start=1):
        line = strip_comment(raw_line).strip()
        if not line:
            continue

        m_org = DIRECTIVE_ORG_RE.match(line)
        if m_org:
            val_str = m_org.group(1).strip()
            addr = parse_number(val_str) if _looks_numeric(val_str) else ctx.resolve(val_str, line_no, raw_line)
            org_seen = True
            continue

        m_word = DIRECTIVE_WORD_RE.match(line)
        if m_word:
            val_str = m_word.group(1).strip()
            raw_items.append(('word', addr, line_no, raw_line, None, val_str))
            addr += 1
            continue

        m_const = CONST_RE.match(line)
        if m_const:
            const_name, val_str = m_const.group(1), m_const.group(2).strip()
            if const_name in ctx.symbols:
                raise AsmError(f"symbolen '{const_name}' redan definierad", line_no, raw_line)
            if _looks_numeric(val_str):
                ctx.symbols[const_name] = parse_number(val_str)
            else:
                # konstant definierad i termer av en annan (redan känd) symbol/konstant
                ctx.symbols[const_name] = ctx.resolve(val_str, line_no, raw_line)
            continue

        m_label = LABEL_RE.match(line)
        if m_label:
            label_name, remainder = m_label.group(1), m_label.group(2).strip()
            if label_name in ctx.symbols:
                raise AsmError(f"symbolen '{label_name}' redan definierad", line_no, raw_line)
            ctx.symbols[label_name] = addr
            if not remainder:
                continue
            line = remainder  # tillåt "label: INSTR ..." på samma rad

        m_instr = INSTR_RE.match(line)
        if not m_instr:
            raise AsmError("kunde inte tolka raden", line_no, raw_line)
        mnemonic_raw, rest = m_instr.group(1), m_instr.group(2)
        mnemonic = mnemonic_raw.upper()
        if mnemonic not in OPCODE_TABLE:
            raise AsmError(f"okänd instruktion '{mnemonic_raw}'", line_no, raw_line)
        operands = split_operands(rest)

        # Vi måste veta hur många ORD instruktionen blir för att räkna nästa adress
        # korrekt INNAN alla symboler är kända (framåtreferenser). Lösning: encodea
        # med en "dummy" ctx där okända symboler ger värde 0 (bara för längden —
        # nästan alla instruktionsformer har FAST längd oavsett operandvärde, så
        # detta är säkert. Undantaget vore om en symbol påverkade VILKEN opcode-
        # variant som väljs, t.ex. JMP REG vs JMP label -- men det avgörs av
        # operandens FORM (skriven syntax), inte symbolens upplösta värde, så
        # längden är ändå deterministisk redan i pass 1a.)
        dummy_ctx = _DummyResolveCtx()
        try:
            words = OPCODE_TABLE[mnemonic](operands, dummy_ctx, line_no, raw_line)
        except AsmError:
            raise
        except Exception as e:
            raise AsmError(f"kodningsfel: {e}", line_no, raw_line)

        raw_items.append(('instr', addr, line_no, raw_line, mnemonic, operands))
        addr += len(words)

    # ── Pass 1b: andra varvet, nu med FULLSTÄNDIG symboltabell ──────────────
    result = {}
    for kind, a, line_no, raw_line, mnemonic, payload in raw_items:
        if kind == 'word':
            val_str = payload
            val = parse_number(val_str) if _looks_numeric(val_str) else ctx.resolve(val_str, line_no, raw_line)
            result[a] = val & 0xFFFF
        else:
            operands = payload
            try:
                words = OPCODE_TABLE[mnemonic](operands, ctx, line_no, raw_line)
            except AsmError:
                raise
            except Exception as e:
                raise AsmError(f"kodningsfel: {e}", line_no, raw_line)
            for i, w in enumerate(words):
                result[a + i] = w & 0xFFFF

    return result, ctx


class _DummyResolveCtx:
    """Används bara i pass 1a för att räkna instruktionslängder utan att
    kräva att alla symboler redan är kända (framåtreferenser)."""
    def resolve(self, name, line_no=None, line_text=None):
        return 0


# ── Output ───────────────────────────────────────────────────────────────────
def write_logisim_img(addr_word_map, filename, rom_size=32768, base_addr=0x8000):
    rom = [0] * rom_size
    for addr, word in addr_word_map.items():
        local = addr - base_addr
        if 0 <= local < rom_size:
            rom[local] = word & 0xFFFF
        else:
            print(f"VARNING: adress ${addr:04X} ligger utanför ROM-fönstret "
                  f"(${base_addr:04X}-${base_addr+rom_size-1:04X}), hoppas över i .img", file=sys.stderr)

    with open(filename, 'w') as f:
        f.write("v3.0 hex words plain\n")
        row = []
        for i in range(rom_size):
            row.append(f"{rom[i]:04x}")
            if len(row) == 8:
                f.write(' '.join(row) + '\n')
                row = []
        if row:
            f.write(' '.join(row) + '\n')


def write_hex_dump(addr_word_map, filename):
    with open(filename, 'w') as f:
        f.write("# PCB-16 program hex dump\n# adress: ord(16-bit hex)\n\n")
        for addr in sorted(addr_word_map):
            f.write(f"${addr:04X}: {addr_word_map[addr]:04X}\n")


def write_raw_bin(addr_word_map, filename, base_addr=0x8000, size=32768):
    """Rå binärfil, little-endian, för ett sammanhängande fönster [base_addr, base_addr+size)."""
    buf = bytearray(size * 2)
    for addr, word in addr_word_map.items():
        local = addr - base_addr
        if 0 <= local < size:
            buf[local*2] = word & 0xFF
            buf[local*2+1] = (word >> 8) & 0xFF
    with open(filename, 'wb') as f:
        f.write(buf)


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Användning: python3 pcb16_asm_v2.py <källfil.asm> [bas-adress hex för .img/.bin, default 8000] [rom-storlek ord, default 32768]")
        sys.exit(1)

    src_path = sys.argv[1]
    base_addr = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x8000
    rom_size = int(sys.argv[3]) if len(sys.argv) > 3 else 32768

    with open(src_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        addr_word_map, ctx = assemble(source)
    except AsmError as e:
        print(f"ASSEMBLERFEL: {e}", file=sys.stderr)
        sys.exit(1)

    base = src_path.rsplit('.', 1)[0]
    img_path = 'assembler\\' + base + '.img'
    hex_path = 'assembler\\' + base + '_hexdump.txt'
    bin_path = 'assembler\\' + base + '.bin'

    write_logisim_img(addr_word_map, img_path, rom_size=rom_size, base_addr=base_addr)
    write_hex_dump(addr_word_map, hex_path)
    write_raw_bin(addr_word_map, bin_path, base_addr=base_addr, size=rom_size)

    print(f"Assemblerade {len(addr_word_map)} ord totalt.")
    print(f"  {img_path}   (Logisim Evolution, {rom_size} ord ROM-lokalt, bas=${base_addr:04X})")
    print(f"  {hex_path}")
    print(f"  {bin_path}")
    if ctx.symbols:
        print("\nSymboler:")
        for name, a in sorted(ctx.symbols.items(), key=lambda kv: kv[1]):
            print(f"  {name:20s} = ${a:04X}")


if __name__ == '__main__':
    main()