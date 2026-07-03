#!/usr/bin/env python3
"""
PCB-16 PopCornBoard — Microcode EEPROM Generator
=================================================
128 opcodes (0x00–0x7F) in exact ISA CSV order.

EEPROM address (15 bits):
  bits  3:0  = STEP   (0–15)
  bits  7:4  = FLAGS  (bit4=CF, bit5=ZF, bit6=NF, bit7=VF)
  bits 14:8  = OPCODE (7 bits)

Control word (48 bits, 42 used):
  Bit  Signal    Description
   0   ARE       ALU A-reg write enable
   1   BRE       ALU B-reg write enable
   2   ADDE      ALU add enable
   3   SUBE      ALU subtract (use with ADDE)
   4   ANDE      ALU bitwise AND enable
   5   XORE      ALU bitwise XOR enable
   6   SHE0      Shift/rotate enable bit 0
   7   SHE1      Shift/rotate enable bit 1
   8   AOE       ALU output enable (→ bus)
   9   MWE       Memory write enable
  10   ADWE      Address register write enable
  11   MOE       Memory output enable (→ bus)
  12   SPE       Stack pointer address enable (SP → MAR)
  13   SCE       Stack pointer count enable
  14   SWE       Stack pointer write enable
  15   SDU       Stack pointer direction (0=decrement/push, 1=increment/pop)
  16   SPOE      Stack pointer output enable (→ bus)
  17   RWE       Register file write enable
  18   RADWE     Register address write enable
  19   JMP       Load PC from bus
  20   CE        PC count enable (PC++)
  21   ROE       Register file output enable (→ bus)
  22   OP0       Operand select bit 0  |
  23   OP1       Operand select bit 1  |> interpreted when OPOE set
  24   OP2       Operand select bit 2  |
  25   OPOE      Operand output enable
  26   INSTDONE  Instruction complete (reset step counter)
  27   INSTWE1   Instruction register 1 write enable
  28   INSTWE2   Instruction register 2 write enable
  29   HLT       Halt clock
  30   PCOE      PC output enable (→ bus)
  31   FLAGE     ALU flags → P register (CF/ZF/NF/VF)
  32   PWE       Bus[3:0] → P register
  33   POE       P register output enable (→ bus)
  34   IWE       Bus[4] → I flip-flop
  35   CWE       Write carry flag
  36   MR        Master reset
  37   CLRINT    Clear interrupt
  38   PINTE     Port interrupt enable
  39   PTADWE    Port address write enable
  40   PTWE      Port write enable
  41   PTOE      Port output enable

SHE encoding (bits 7:6):
  00 = no shift
  01 = LSR (logical right shift)
  10 = ROR (rotate right through carry)
  11 = ROL (rotate left through carry)
  LSL is implemented as ADDE with A=B=X (X+X)

OP field (bits 24:22) when OPOE asserted:
  000 (0) = REG X address   (3-bit field from byte1 encoding)
  001 (1) = REG Y address   (3-bit field from byte2 encoding)
  010 (2) = REG Z address   (3-bit field from byte2 encoding)
  011 (3) = 16-bit DATA/ADDR with register operand  (IR2, bytes 3-4)
  100 (4) = 16-bit ADDR without register operand    (IR2, bytes 2-3 of 4-byte instr)

Registers: A=000, B=001, C=010, D=011, E=100, F=101, G=110, H=111
Stack: $0000–$00FF, grows down (push = write then decrement, pop = increment then read)
Reset vector: $FFFE–$FFFF
Port interrupt address: $FFED–$FFFD
"""

# ── Control signal bit masks ─────────────────────────────────────────────────
# KICAD BUS: MICROCODE{ARE BRE ADDE SUBE ANDE XORE SHE0 SHE1 AOE MWE ADWE MOE SPE SCE SWE SDU SPOE RWE RADWE JMP CE ROE OP0 OP1 OP2 OPOE INSTDONE INSTWE1 INSTWE2 HLT PCOE FLAGE PWE POE IWE CWE MR CLRINT PINTE PTADWE PTWE PTOE}
ARE      = 1 << 0
BRE      = 1 << 1
ADDE     = 1 << 2
SUBE     = 1 << 3
ANDE     = 1 << 4
XORE     = 1 << 5
SHE0     = 1 << 6
SHE1     = 1 << 7
AOE      = 1 << 8
MWE      = 1 << 9
ADWE     = 1 << 10
MOE      = 1 << 11
SPE      = 1 << 12
SCE      = 1 << 13
SWE      = 1 << 14
SDU      = 1 << 15
SPOE     = 1 << 16
RWE      = 1 << 17
RADWE    = 1 << 18
JMP      = 1 << 19
CE       = 1 << 20
ROE      = 1 << 21
OP0      = 1 << 22
OP1      = 1 << 23
OP2      = 1 << 24
OPOE     = 1 << 25
INSTDONE = 1 << 26
INSTWE1  = 1 << 27
INSTWE2  = 1 << 28
HLT      = 1 << 29
PCOE     = 1 << 30
FLAGE    = 1 << 31
PWE      = 1 << 32
POE      = 1 << 33
IWE      = 1 << 34
CWE      = 1 << 35
MR       = 1 << 36
CLRINT   = 1 << 37
PINTE    = 1 << 38
PTADWE   = 1 << 39
PTWE     = 1 << 40
PTOE     = 1 << 41

# ── OP field helpers ──────────────────────────────────────────────────────────
def OP(n):
    return (n & 0b111) << 22

OP_REGX  = OP(0)  # reg X addr on bus
OP_REGY  = OP(1)  # reg Y addr on bus
OP_REGZ  = OP(2)  # reg Z addr on bus
OP_DATA  = OP(3)  # 16-bit immediate (with reg operand encoding)
OP_ADDR  = OP(4)  # 16-bit address   (without reg operand encoding)
OP_EXTRA = OP(5)  # 3-bit extra data (baked by assembler)
OP_FFFF  = OP(6)  # $FFFF on bus

# SHE combos
SHE_LSR = SHE0
SHE_ROR = SHE1
SHE_ROL = SHE0 | SHE1

# ── ROM ───────────────────────────────────────────────────────────────────────
rom = [0] * 32768  # 15-bit address, 40-bit data

def rom_addr(opcode, flags, step):
    return (opcode << 8) | (flags << 4) | step

def write(opcode, steps, flag_mask=0, flag_val=0):
    """Write microcode steps for an opcode, optionally restricted to flag combinations."""
    assert len(steps) <= 16, f"opcode 0x{opcode:02X}: {len(steps)} steps > 16"
    for flags in range(16):
        if flag_mask and (flags & flag_mask) != (flag_val & flag_mask):
            continue
        for step, cw in enumerate(steps):
            rom[rom_addr(opcode, flags, step)] = cw

def write_all(opcode, steps):
    write(opcode, steps)

# ── Reusable fetch sequences ──────────────────────────────────────────────────
# 1-byte instruction: fetch opcode only (1 word)
F1 = [
    PCOE | ADWE,           # PC → MAR
    MOE | INSTWE1 | CE,    # mem[PC] → IR1, PC++
]

# 2-byte instruction: opcode + 1 operand word
F2 = [
    PCOE | ADWE,           # PC → MAR
    MOE | INSTWE1 | CE,    # mem[PC] → IR1, PC++
    PCOE | ADWE,           # PC → MAR
    MOE | INSTWE2 | CE,    # mem[PC] → IR2, PC++
]

# ── Signal names for listing ──────────────────────────────────────────────────
SIG_NAMES = [
    (ARE,      'ARE'),
    (BRE,      'BRE'),
    (ADDE,     'ADDE'),
    (SUBE,     'SUBE'),
    (ANDE,     'ANDE'),
    (XORE,     'XORE'),
    (SHE0,     'SHE0'),
    (SHE1,     'SHE1'),
    (AOE,      'AOE'),
    (MWE,      'MWE'),
    (ADWE,     'ADWE'),
    (MOE,      'MOE'),
    (SPE,      'SPE'),
    (SCE,      'SCE'),
    (SWE,      'SWE'),
    (SDU,      'SDU'),
    (SPOE,     'SPOE'),
    (RWE,      'RWE'),
    (RADWE,    'RADWE'),
    (JMP,      'JMP'),
    (CE,       'CE'),
    (ROE,      'ROE'),
    (INSTDONE, 'INSTDONE'),
    (INSTWE1,  'INSTWE1'),
    (INSTWE2,  'INSTWE2'),
    (HLT,      'HLT'),
    (PCOE,     'PCOE'),
    (FLAGE,    'FLAGE'),
    (PWE,      'PWE'),
    (POE,      'POE'),
    (IWE,      'IWE'),
    (CWE,      'CWE'),
    (MR,       'MR'),
    (CLRINT,   'CLRINT'),
    (PINTE,    'PINTE'),
    (PTADWE,   'PTADWE'),
    (PTWE,     'PTWE'),
    (PTOE,     'PTOE'),
]

def decode_cw(cw):
    names = []
    for mask, name in SIG_NAMES:
        if cw & mask:
            names.append(name)
    if cw & OPOE:
        op_val = (cw >> 22) & 0b111
        op_names = ['OP_REGX','OP_REGY','OP_REGZ','OP_DATA','OP_ADDR','OP_EXTRA','OP_FFFF']
        names.append('OPOE')
        names.append(op_names[op_val] if op_val < len(op_names) else f'OP({op_val})')
    return names

# ═══════════════════════════════════════════════════════════════════════════════
# MICROCODE DEFINITIONS  (0x00 – 0x7F in CSV order)
# ═══════════════════════════════════════════════════════════════════════════════

# ── 0x00 RST ──────────────────────────────────────────────────────────────────
write_all(0x00, F1 + [
    MR | FLAGE | CWE | PWE,
    OP_FFFF | OPOE | ADWE | SCE,
    MOE | JMP,
    INSTDONE,
])

# ── 0x01 HLT ──────────────────────────────────────────────────────────────────
write_all(0x01, F1 + [HLT | INSTDONE])

# ── 0x02 BRK — software interrupt ────────────────────────────────────────────
# Push PC to stack, push P, load BRK vector from $FFFF
write_all(0x02, F2 + [
    PCOE | SPE | MWE | CLRINT,  # mem[SP] = PC (return address)
    SCE,                        # SP--
    POE | SPE | MWE,           # mem[SP] = P
    SCE,                        # SP--
    # Load BRK vector address ($FFFF) hardcoded via assembler in IR2
    OPOE | OP_ADDR | ADWE,     # IR2 (BRK vector ptr) → MAR
    MOE | JMP,
    INSTDONE,       # PC = vector
])

# ── 0x03 WAI — wait for interrupt ────────────────────────────────────────────
write_all(0x03, F1 + [HLT | INSTDONE])

# ── ALU helper builders ───────────────────────────────────────────────────────
def alu_3reg(opcode, alu_sig, writeback=True):
    """Z = X op Y  (register mode, 1-word fetch)"""
    steps = list(F1) + [
        OPOE | OP_REGX | RADWE,    # latch X addr
        ROE | ARE,                  # Reg[X] → ALU A
        OPOE | OP_REGY | RADWE,    # latch Y addr
        ROE | BRE,                  # Reg[Y] → ALU B
    ]
    if writeback:
        steps += [
            OPOE | OP_REGZ | RADWE,        # latch Z addr
            alu_sig | FLAGE | CWE | AOE | RWE,   # compute, latch flags
            INSTDONE,                 # Reg[Z] = result
        ]
    else:
        steps += [alu_sig | FLAGE | CWE, INSTDONE]
    write_all(opcode, steps)

def alu_imm(opcode, alu_sig, writeback=True):
    """X = X op DATA  (immediate, 2-byte fetch: byte1=opcode+REGX, byte2=DATA)"""
    steps = list(F2) + [
        OPOE | OP_REGX | RADWE,
        ROE | ARE,                  # Reg[X] → ALU A
        OPOE | OP_DATA | BRE,      # DATA from IR2 → ALU B
    ]
    if writeback:
        steps += [
            OPOE | OP_REGX | RADWE,        # writeback to X (not Z)
            alu_sig | FLAGE | CWE | AOE | RWE,   # compute, latch flags
            INSTDONE,                 # Reg[Z] = result
        ]
    else:
        steps += [alu_sig | FLAGE | CWE, INSTDONE]
    write_all(opcode, steps)

def alu_abs(opcode, alu_sig, writeback=True):
    """X = X op mem[ADDRESS]  (absolute, 2-byte fetch: byte1=opcode+REGX, byte2=ADDRESS)"""
    steps = list(F2) + [
        OPOE | OP_REGX | RADWE,
        ROE | ARE,
        OPOE | OP_DATA | ADWE,     # ADDRESS → MAR
        MOE | BRE,                  # mem[ADDRESS] → ALU B
    ]
    if writeback:
        steps += [
            OPOE | OP_REGX | RADWE,
            alu_sig | AOE | FLAGE | CWE | RWE,
            INSTDONE,
        ]
    else:
        steps += [alu_sig | FLAGE | CWE, INSTDONE]
    write_all(opcode, steps)

def alu_idxA(opcode, alu_sig, writeback=True):
    """X = X op mem[ADDRESS + A]"""
    steps = list(F2) + [
        OPOE | OP_DATA | ARE,       # ADDRESS → ALU A
        OPOE | OP_EXTRA | RADWE,    # latch A-register address (assembler bakes A=001 in EXTRA slot)
        ROE | BRE,                  # Reg[A] → ALU B
        ADDE | AOE | ADWE,          # EA → MAR
        MOE | BRE,
        OPOE | OP_REGX | RADWE,     # reload source reg
        ROE | ARE,
    ]
    if writeback:
        steps += [
            OPOE | OP_REGX | RADWE,
            alu_sig | AOE | FLAGE | CWE | RWE,
            INSTDONE,
        ]
    else:
        steps += [alu_sig | FLAGE | CWE, INSTDONE]
    write_all(opcode, steps)

def alu_idxB(opcode, alu_sig, writeback=True):
    """X = X op mem[ADDRESS + B]"""
    steps = list(F2) + [
        OPOE | OP_DATA | ARE,       # ADDRESS → ALU A
        OPOE | OP_EXTRA | RADWE,    # latch B-register address (assembler bakes B=001 in REGY slot)
        ROE | BRE,                  # Reg[B] → ALU B
        ADDE | AOE | ADWE,          # EA → MAR
        MOE | BRE,
        OPOE | OP_REGX | RADWE,     # reload source reg
        ROE | ARE,
    ]
    if writeback:
        steps += [
            OPOE | OP_REGX | RADWE,
            alu_sig | AOE | FLAGE | CWE | RWE,
            INSTDONE,
        ]
    else:
        steps += [alu_sig | FLAGE | CWE, INSTDONE]
    write_all(opcode, steps)

# ── 0x04–0x08  ADC R/# /a/(a,a)/(a,b) ────────────────────────────────────────
alu_3reg(0x04, ADDE)
alu_imm (0x05, ADDE)
alu_abs (0x06, ADDE)
alu_idxA(0x07, ADDE)
alu_idxB(0x08, ADDE)

# ── 0x09–0x0D  SBC R/#/a/(a,a)/(a,b) ─────────────────────────────────────────
alu_3reg(0x09, ADDE | SUBE)
alu_imm (0x0A, ADDE | SUBE)
alu_abs (0x0B, ADDE | SUBE)
alu_idxA(0x0C, ADDE | SUBE)
alu_idxB(0x0D, ADDE | SUBE)

# ── 0x0E–0x11  NEG R/a/(a,a)/(a,b) ──────────────────────────────────────────
# NEG R: X = 0 - X
write_all(0x0E, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | BRE,                   # Reg[X] → ALU B
    ARE,                         # clear ALU A (drive 0 — no OE, A-reg zeroed)
    OPOE | OP_REGX | RADWE,
    ADDE | SUBE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])

# NEG a
write_all(0x0F, list(F2) + [
    OPOE | OP_ADDR | ADWE,     # ADDRESS → MAR
    MOE | BRE,
    ARE,
    ADDE | SUBE | FLAGE | CWE | AOE | MWE,
    INSTDONE,
])

# NEG (a,a)
write_all(0x10, list(F2) + [
    OPOE | OP_ADDR | ARE,
    OPOE | OP_EXTRA | RADWE,    # Reg[A] addr
    ROE | BRE,
    ADDE | AOE | ADWE,          # EA → MAR
    MOE | BRE,
    ARE,
    ADDE | SUBE | FLAGE | CWE | AOE | MWE,
    INSTDONE,
])

# NEG (a,b)
write_all(0x11, list(F2) + [
    OPOE | OP_ADDR | ARE,
    OPOE | OP_EXTRA | RADWE,    # Reg[B] addr
    ROE | BRE,
    ADDE | AOE | ADWE,          # EA → MAR
    MOE | BRE,
    ARE,
    ADDE | SUBE | FLAGE | CWE | AOE | MWE,
    INSTDONE,
])

# ── 0x12–0x16  OR R/#/a/(a,a)/(a,b) ─────────────────────────────────────────
# OR = ANDE|XORE  (A|B = (A&B) XOR (A^B)... actually OR via De Morgan: not quite)
# More correctly: the ALU implements OR as a separate operation or via ANDE+XORE
# Per spec the ALU has AND and XOR gates; OR = (A AND B) OR (A XOR B)... 
# Actually OR = NOT(NOT A AND NOT B) which needs NOT. 
# Simplest assumption: ALU has dedicated OR mode via ANDE|XORE combination (CORRECT)
# (common in SAP-2 style designs where OR is derived). Using that here.
alu_3reg(0x12, ANDE | XORE)
alu_imm (0x13, ANDE | XORE)
alu_abs (0x14, ANDE | XORE)
alu_idxA(0x15, ANDE | XORE)
alu_idxB(0x16, ANDE | XORE)

# ── 0x17–0x1B  AND R/#/a/(a,a)/(a,b) ────────────────────────────────────────
alu_3reg(0x17, ANDE)
alu_imm (0x18, ANDE)
alu_abs (0x19, ANDE)
alu_idxA(0x1A, ANDE)
alu_idxB(0x1B, ANDE)

# ── 0x1C–0x20  XOR R/#/a/(a,a)/(a,b) ────────────────────────────────────────
alu_3reg(0x1C, XORE)
alu_imm (0x1D, XORE)
alu_abs (0x1E, XORE)
alu_idxA(0x1F, XORE)
alu_idxB(0x20, XORE)

# ── 0x21–0x25  CMP R/#/a/(a,a)/(a,b) — no writeback ─────────────────────────
alu_3reg(0x21, ADDE | SUBE, writeback=False)
alu_imm (0x22, ADDE | SUBE, writeback=False)
alu_abs (0x23, ADDE | SUBE, writeback=False)
alu_idxA(0x24, ADDE | SUBE, writeback=False)
alu_idxB(0x25, ADDE | SUBE, writeback=False)

# ── 0x26–0x2A  BIT R/#/a/(a,a)/(a,b) — no writeback ─────────────────────────
alu_3reg(0x26, ANDE, writeback=False)
alu_imm (0x27, ANDE, writeback=False)
alu_abs (0x28, ANDE, writeback=False)
alu_idxA(0x29, ANDE, writeback=False)
alu_idxB(0x2A, ANDE, writeback=False)

# ── Unary memory helper ───────────────────────────────────────────────────────
def unary_mem(opcode, alu_sig, she_sig=0, is_lsl=False):
    """
    mem[EA] = unary_op(mem[EA])  for absolute addressing.
    alu_sig: ALU operation signals
    she_sig: shift enable bits (0 for add-based ops)
    is_lsl:  use A+A instead of shift
    EA is in OP_DATA (absolute address from IR2).
    """
    
    if she_sig:
        steps = list(F2) + [
            OPOE | OP_DATA | ADWE,
            MOE | ARE,
            she_sig | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    elif is_lsl:
        steps = list(F2) + [
            OPOE | OP_DATA | ADWE,
            MOE | ARE | BRE,
            ADDE | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    else:  # INC/DEC use alu_sig = ADDE or ADDE|SUBE with B=1
        steps = list(F2) + [
            OPOE | OP_DATA | ADWE,
            MOE | ARE,
            OPOE | OP_EXTRA | BRE,
            alu_sig | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    write_all(opcode, steps)

def unary_mem_idx(opcode, alu_sig, idx_reg_op, she_sig=0, is_lsl=False):
    """
    mem[ADDRESS + Reg[idx]] = unary_op(mem[ADDRESS + Reg[idx]])
    idx_reg_op: OPOE|OP_EXTRA for A/B index
    """
    def ea_steps():
        return [
            OPOE | OP_DATA | ARE,
            idx_reg_op | RADWE,
            ROE | BRE,
            ADDE | AOE | ADWE,
        ]
    if she_sig:
        steps = list(F2) + ea_steps() + [
            MOE | ARE | BRE,
            she_sig | ANDE | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    elif is_lsl:
        steps = list(F2) + ea_steps() + [
            MOE | ARE | BRE,
            ADDE | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    else:
        steps = list(F2) + ea_steps() + [
            MOE | ARE,
            OPOE | OP_EXTRA | BRE,
            alu_sig | FLAGE | CWE | AOE | MWE,
            INSTDONE,
        ]
    write_all(opcode, steps)

# ── 0x2B–0x2E  INC R/a/(a,a)/(a,b) ──────────────────────────────────────────
# INC R: X = X + 1  (assembler bakes 1 in REGY slot of first word)
write_all(0x2B, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE,
    OPOE | OP_REGY | BRE,      # 1 baked by assembler into REGY field of first word
    ADDE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x2C, ADDE)
unary_mem_idx(0x2D, ADDE, OPOE | OP_EXTRA)
unary_mem_idx(0x2E, ADDE, OPOE | OP_EXTRA)

# ── 0x2F–0x32  DEC R/a/(a,a)/(a,b) ──────────────────────────────────────────
write_all(0x2F, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE,
    OPOE | OP_REGY | BRE,      # 1 baked by assembler into REGY field of first word
    ADDE | SUBE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x30, ADDE | SUBE)
unary_mem_idx(0x31, ADDE | SUBE, OPOE | OP_EXTRA)
unary_mem_idx(0x32, ADDE | SUBE, OPOE | OP_EXTRA)

# ── 0x33–0x36  LSR R/a/(a,a)/(a,b) ──────────────────────────────────────────
write_all(0x33, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE | BRE,
    OPOE | OP_REGX | RADWE,
    SHE_LSR | ANDE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x34, 0, she_sig=SHE_LSR)
unary_mem_idx(0x35, 0, OPOE | OP_EXTRA, she_sig=SHE_LSR)
unary_mem_idx(0x36, 0, OPOE | OP_EXTRA, she_sig=SHE_LSR)

# ── 0x37–0x3A  LSL R/a/(a,a)/(a,b) ──────────────────────────────────────────
write_all(0x37, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE | BRE,
    OPOE | OP_REGX | RADWE,
    ADDE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x38, 0, is_lsl=True)
unary_mem_idx(0x39, 0, OPOE | OP_EXTRA, is_lsl=True)
unary_mem_idx(0x3A, 0, OPOE | OP_EXTRA, is_lsl=True)

# ── 0x3B–0x3E  ROR R/a/(a,a)/(a,b) ──────────────────────────────────────────
write_all(0x3B, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE | BRE,
    OPOE | OP_REGX | RADWE,
    SHE_ROR | ANDE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x3C, 0, she_sig=SHE_ROR)
unary_mem_idx(0x3D, 0, OPOE | OP_EXTRA, she_sig=SHE_ROR)
unary_mem_idx(0x3E, 0, OPOE | OP_EXTRA, she_sig=SHE_ROR)

# ── 0x3F–0x42  ROL R/a/(a,a)/(a,b) ──────────────────────────────────────────
write_all(0x3F, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | ARE | BRE,
    OPOE | OP_REGX | RADWE,
    SHE_ROL | ANDE | FLAGE | CWE | AOE | RWE,
    INSTDONE,
])
unary_mem    (0x40, 0, she_sig=SHE_ROL)
unary_mem_idx(0x41, 0, OPOE | OP_EXTRA, she_sig=SHE_ROL)
unary_mem_idx(0x42, 0, OPOE | OP_EXTRA, she_sig=SHE_ROL)

# ── 0x43–0x47  PHR R/#/a/(a,a)/(a,b) — push to stack ────────────────────────
# PHR R: push Reg[X]
write_all(0x43, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | SPE | MWE,           # mem[SP] = Reg[X]
    SCE,
    INSTDONE,             # SP--
])

# PHR #: push immediate DATA
write_all(0x44, list(F2) + [
    OPOE | OP_DATA | SPE | MWE,  # mem[SP] = DATA
    SCE,
    INSTDONE,
])

# PHR a: push mem[ADDRESS]
write_all(0x45, list(F2) + [
    OPOE | OP_DATA | ADWE,
    MOE | SPE | MWE,
    SCE,
    INSTDONE,
])

# PHR (a,a): push mem[ADDRESS + A]
write_all(0x46, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,    # A index
    ROE | BRE,
    ADDE | AOE | ADWE,
    MOE | SPE | MWE,
    SCE,
    INSTDONE,
])

# PHR (a,b): push mem[ADDRESS + B]
write_all(0x47, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,    # B index
    ROE | BRE,
    ADDE | AOE | ADWE,
    MOE | SPE | MWE,
    SCE,
    INSTDONE,
])

# ── 0x48–0x4B  PLR R/a/(a,a)/(a,b) — pull from stack ────────────────────────
# PLR R: pop → Reg[X]
write_all(0x48, list(F1) + [
    SCE | SDU,                 # SP++
    OPOE | OP_REGX | RADWE,
    SPE | MOE | RWE,
    INSTDONE,
])

# PLR a: pop → mem[ADDRESS]
write_all(0x49, list(F2) + [
    SCE | SDU,
    OPOE | OP_DATA | ADWE,     # ADDRESS → MAR
    SPE | MOE | MWE,
    INSTDONE,      # mem[ADDRESS] = popped value
])

# PLR (a,a): pop → mem[ADDRESS + A]
write_all(0x4A, list(F2) + [
    SCE | SDU,
    OPOE | OP_DATA | ARE,     # ADDRESS → MAR
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    SPE | MOE | MWE,
    INSTDONE,      # mem[ADDRESS] = popped value
])

# PLR (a,b): pop → mem[ADDRESS + B]
write_all(0x4B, list(F2) + [
    SCE | SDU,
    OPOE | OP_DATA | ARE,     # ADDRESS → MAR
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    SPE | MOE | MWE,
    INSTDONE,      # mem[ADDRESS] = popped value
])

# ── 0x4C PHP — push P ─────────────────────────────────────────────────────────
write_all(0x4C, list(F1) + [
    POE | SPE | MWE,
    SCE,
    INSTDONE,
])

# ── 0x4D PLP — pull P ─────────────────────────────────────────────────────────
write_all(0x4D, list(F1) + [
    SCE | SDU,
    SPE | MOE | PWE | CWE | IWE,
    INSTDONE,
])

# ── 0x4E PEK r (DATA form) — peek stack at offset DATA into Reg[X] ───────────
# SP + DATA → X  i.e. Reg[X] = mem[SP + DATA]
write_all(0x4E, list(F2) + [
    SPOE | ARE,                # SP → ALU A
    OPOE | OP_DATA | BRE,     # DATA → ALU B
    ADDE | AOE | ADWE,         # EA = SP + DATA → MAR
    OPOE | OP_REGX | RADWE,
    MOE | RWE,
    INSTDONE,
])

# ── 0x4F PEK r (register form) — SP[X] → Z ───────────────────────────────────
# Reg[Z] = mem[SP + Reg[X]]
write_all(0x4F, list(F1) + [
    SPOE | ARE,                # SP → ALU A
    OPOE | OP_REGX | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,         # EA = SP + DATA → MAR
    OPOE | OP_REGY | RADWE,
    MOE | RWE,
    INSTDONE,
])

# ── Branch helper ─────────────────────────────────────────────────────────────
# flag nibble bits: CF=bit0, ZF=bit1, NF=bit2, VF=bit3
F_CF = 1; F_ZF = 2; F_NF = 4; F_VF = 8

def branch_rel(opcode, flag_bit, branch_if_set):
    """Relative branch: PC = PC + DATA (signed)"""
    mask = flag_bit
    taken_val = flag_bit if branch_if_set else 0
    fetch = list(F2)
    # Taken: PC = PC + DATA
    taken = fetch + [
        PCOE | ARE,                # current PC → ALU A
        OPOE | OP_DATA | BRE,     # DATA (signed offset) → ALU B
        ADDE | AOE | JMP,
        INSTDONE,
    ]
    # Not taken: just done
    not_taken = fetch + [INSTDONE]
    write(opcode, taken,     flag_mask=mask, flag_val=taken_val)
    write(opcode, not_taken, flag_mask=mask, flag_val=taken_val ^ mask)

def branch_abs(opcode, flag_bit, branch_if_set):
    """Absolute branch: PC = ADDRESS"""
    mask = flag_bit
    taken_val = flag_bit if branch_if_set else 0
    fetch = list(F2)
    taken     = fetch + [OPOE | OP_DATA | JMP, INSTDONE]
    not_taken = fetch + [INSTDONE]
    write(opcode, taken,     flag_mask=mask, flag_val=taken_val)
    write(opcode, not_taken, flag_mask=mask, flag_val=taken_val ^ mask)

# ── 0x50–0x5F  BNE/BEQ/BCC/BCS/BPL/BMI/BVC/BVS  r/a ─────────────────────────
branch_rel(0x50, F_ZF, branch_if_set=False)  # BNE r  (Z=0)
branch_abs(0x51, F_ZF, branch_if_set=False)  # BNE a
branch_rel(0x52, F_ZF, branch_if_set=True)   # BEQ r  (Z=1)
branch_abs(0x53, F_ZF, branch_if_set=True)   # BEQ a
branch_rel(0x54, F_CF, branch_if_set=False)  # BCC r  (C=0)
branch_abs(0x55, F_CF, branch_if_set=False)  # BCC a
branch_rel(0x56, F_CF, branch_if_set=True)   # BCS r  (C=1)
branch_abs(0x57, F_CF, branch_if_set=True)   # BCS a
branch_rel(0x58, F_NF, branch_if_set=False)  # BPL r  (N=0)
branch_abs(0x59, F_NF, branch_if_set=False)  # BPL a
branch_rel(0x5A, F_NF, branch_if_set=True)   # BMI r  (N=1)
branch_abs(0x5B, F_NF, branch_if_set=True)   # BMI a
branch_rel(0x5C, F_VF, branch_if_set=False)  # BVC r  (V=0)
branch_abs(0x5D, F_VF, branch_if_set=False)  # BVC a
branch_rel(0x5E, F_VF, branch_if_set=True)   # BVS r  (V=1)
branch_abs(0x5F, F_VF, branch_if_set=True)   # BVS a

# ── 0x60–0x64  JMP R/r/a/(a,a)/(a,b) ─────────────────────────────────────────
# JMP R: PC = Reg[X]
write_all(0x60, list(F1) + [
    OPOE | OP_REGX | RADWE,
    ROE | JMP,
    INSTDONE,
])

# JMP r: PC = PC + DATA (relative)
write_all(0x61, list(F2) + [
    PCOE | ARE,
    OPOE | OP_ADDR | BRE,
    ADDE | AOE | JMP,
    INSTDONE,
])

# JMP a: PC = ADDRESS
write_all(0x62, list(F2) + [
    OPOE | OP_ADDR | JMP,
    INSTDONE,
])

# JMP (a,a): PC = ADDRESS + A
write_all(0x63, list(F2) + [
    OPOE | OP_ADDR | ARE,
    OPOE | OP_EXTRA | RADWE,    # A index
    ROE | BRE,
    ADDE | AOE | JMP,
    INSTDONE,
])

# JMP (a,b): PC = ADDRESS + B
write_all(0x64, list(F2) + [
    OPOE | OP_ADDR | ARE,
    OPOE | OP_EXTRA | RADWE,    # B index
    ROE | BRE,
    ADDE | AOE | JMP,
    INSTDONE,
])

# ── 0x65 JSR a — jump to subroutine ──────────────────────────────────────────
write_all(0x65, list(F2) + [
    PCOE | SPE | MWE,          # mem[SP] = PC (return address, already past instruction)
    SCE | OPOE | OP_DATA | JMP,
    INSTDONE,  # PC = ADDRESS
])

# ── 0x66 RTS — return from subroutine ────────────────────────────────────────
write_all(0x66, list(F1) + [
    SCE | SDU,                 # SP++
    SPE | MOE | JMP,
    INSTDONE,
])

# ── 0x67 RTI — return from interrupt ─────────────────────────────────────────
write_all(0x67, list(F1) + [
    SCE | SDU,                 # SP++ (pop P)
    SPE | MOE | PWE | CWE | IWE,    # P = mem[SP]
    SCE | SDU,                 # SP++ (pop PC)
    SPE | MOE | JMP,
    INSTDONE,
])

# ── 0x68–0x6B  LDR #/a/(a,a)/(a,b) ──────────────────────────────────────────
# LDR #: Reg[X] = DATA
write_all(0x68, list(F2) + [
    OPOE | OP_REGX | RADWE,
    OPOE | OP_DATA | RWE,
    INSTDONE,
])

# LDR a: Reg[X] = mem[ADDRESS]
write_all(0x69, list(F2) + [
    OPOE | OP_DATA | ADWE,
    OPOE | OP_REGX | RADWE,
    MOE | RWE,
    INSTDONE,
])

# LDR (a,a): Reg[X] = mem[ADDRESS + A]
write_all(0x6A, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,    # A index baked by assembler
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | RADWE,
    MOE | RWE,
    INSTDONE,
])

# LDR (a,b): Reg[X] = mem[ADDRESS + B]
write_all(0x6B, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,    # B index
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | RADWE,
    MOE | RWE,
    INSTDONE,
])

# ── 0x6C–0x6E  STR a/(a,a)/(a,b) ────────────────────────────────────────────
# STR a: mem[ADDRESS] = Reg[X]
write_all(0x6C, list(F2) + [
    OPOE | OP_DATA | ADWE,
    OPOE | OP_REGX | RADWE,
    ROE | MWE,
    INSTDONE,
])

# STR (a,a): mem[ADDRESS + A] = Reg[X]
write_all(0x6D, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | RADWE,
    ROE | MWE,
    INSTDONE,
])

# STR (a,b): mem[ADDRESS + B] = Reg[X]
write_all(0x6E, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | RADWE,
    ROE | MWE,
    INSTDONE,
])

# ── 0x6F–0x73  PSD R/#/a/(a,a)/(a,b) — send to port ─────────
# PORT is a 3-bit address (encoded as REG field without reg).
# PSD R: port[PORT] = Reg[X]
write_all(0x6F, list(F1) + [
    OPOE | OP_REGX | PTADWE,
    OPOE | OP_REGY | RADWE,
    ROE | PTWE,
    INSTDONE,
])

# PSD #: port[PORT] = DATA
write_all(0x70, list(F2) + [
    OPOE | OP_REGX | PTADWE,
    OPOE | OP_DATA | RADWE,
    ROE | PTWE,
    INSTDONE,
])

# PSD a: port[PORT] = mem[ADDRESS]
write_all(0x71, list(F2) + [
    OPOE | OP_REGX | PTADWE,
    OPOE | OP_DATA | ADWE,
    MOE | PTWE,
    INSTDONE,
])

# PSD (a,a): port[PORT] = mem[ADDRESS + A]
write_all(0x72, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | PTADWE,
    MOE | PTWE,
    INSTDONE,
])

# PSD (a,b): port[PORT] = mem[ADDRESS + B]
write_all(0x73, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | PTADWE,
    MOE | PTWE,
    INSTDONE,
])

# ── 0x74–0x77  PRD R/a/(a,a)/(a,b) — read from port ─────────────────────────
# PRD R: Reg[X] = port[PORT]
write_all(0x74, list(F1) + [
    OPOE | OP_REGX | PTADWE,
    OPOE | OP_REGY | RADWE,
    PTOE | RWE,
    INSTDONE,
])

# PRD a: mem[ADDRESS] = port[PORT]
write_all(0x75, list(F2) + [
    OPOE | OP_REGX | PTADWE,
    OPOE | OP_DATA | ADWE,
    PTOE | MWE,
    INSTDONE,
])

# PRD (a,a): mem[ADDRESS + A] = port[PORT]
# Use Reg[X] as temp: read port → Reg[X], compute EA, write Reg[X] → mem[EA]
write_all(0x76, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | PTADWE,
    PTOE | MWE,
    INSTDONE,
])

# PRD (a,b): mem[ADDRESS + B] = port[PORT]
write_all(0x77, list(F2) + [
    OPOE | OP_DATA | ARE,
    OPOE | OP_EXTRA | RADWE,
    ROE | BRE,
    ADDE | AOE | ADWE,
    OPOE | OP_REGX | PTADWE,
    PTOE | MWE,
    INSTDONE,
])

# ── 0x78 PIE — port interrupt enable ────────────────────────────────────────
# I -> INT[PORT]: write I to interrupt enable for PORT
write_all(0x78, list(F1) + [
    OPOE | OP_REGX | PINTE,
    INSTDONE,
])

# ── 0x79 CLI — clear interrupt disable ───────────────────────────────────────
write_all(0x79, list(F1) + [
    IWE,
    INSTDONE,
])

# ── 0x7A SEI — set interrupt disable ─────────────────────────────────────────
write_all(0x7A, list(F1) + [
    OPOE | OP_REGX | IWE,
    INSTDONE,
])

# ── 0x7B CLC — clear carry ───────────────────────────────────────────────────
write_all(0x7B, list(F1) + [
    CWE,
    INSTDONE,
])

# ── 0x7C SEC — set carry ──────────────────────────────────────────────────────
write_all(0x7C, list(F1) + [
    OPOE | OP_REGX | CWE,
    INSTDONE,
])

# ── 0x7D CPC R — copy PC to register ─────────────────────────────────────────
write_all(0x7D, list(F1) + [
    OPOE | OP_REGX | RADWE,
    PCOE | RWE,
    INSTDONE,
])

# ── 0x7E CSP R — copy SP to register ─────────────────────────────────────────
write_all(0x7E, list(F1) + [
    OPOE | OP_REGX | RADWE,
    SPOE | RWE,
    INSTDONE,
])

# ── 0x7F CPR R — copy P to register ──────────────────────────────────────────
write_all(0x7F, list(F1) + [
    OPOE | OP_REGX | RADWE,
    POE | RWE,
    INSTDONE,
])

# ══════════════════════════════════════════════════════════════════════════════
# OPCODE NAME TABLE (in CSV order)
# ══════════════════════════════════════════════════════════════════════════════
OPCODE_NAMES = {
    0x00: 'RST',        0x01: 'HLT',        0x02: 'BRK a',      0x03: 'WAI',
    0x04: 'ADC R',      0x05: 'ADC #',      0x06: 'ADC a',      0x07: 'ADC (a,a)',  0x08: 'ADC (a,b)',
    0x09: 'SBC R',      0x0A: 'SBC #',      0x0B: 'SBC a',      0x0C: 'SBC (a,a)',  0x0D: 'SBC (a,b)',
    0x0E: 'NEG R',      0x0F: 'NEG a',      0x10: 'NEG (a,a)',  0x11: 'NEG (a,b)',
    0x12: 'OR R',       0x13: 'OR #',       0x14: 'OR a',       0x15: 'OR (a,a)',   0x16: 'OR (a,b)',
    0x17: 'AND R',      0x18: 'AND #',      0x19: 'AND a',      0x1A: 'AND (a,a)',  0x1B: 'AND (a,b)',
    0x1C: 'XOR R',      0x1D: 'XOR #',      0x1E: 'XOR a',      0x1F: 'XOR (a,a)',  0x20: 'XOR (a,b)',
    0x21: 'CMP R',      0x22: 'CMP #',      0x23: 'CMP a',      0x24: 'CMP (a,a)',  0x25: 'CMP (a,b)',
    0x26: 'BIT R',      0x27: 'BIT #',      0x28: 'BIT a',      0x29: 'BIT (a,a)',  0x2A: 'BIT (a,b)',
    0x2B: 'INC R',      0x2C: 'INC a',      0x2D: 'INC (a,a)',  0x2E: 'INC (a,b)',
    0x2F: 'DEC R',      0x30: 'DEC a',      0x31: 'DEC (a,a)',  0x32: 'DEC (a,b)',
    0x33: 'LSR R',      0x34: 'LSR a',      0x35: 'LSR (a,a)',  0x36: 'LSR (a,b)',
    0x37: 'LSL R',      0x38: 'LSL a',      0x39: 'LSL (a,a)',  0x3A: 'LSL (a,b)',
    0x3B: 'ROR R',      0x3C: 'ROR a',      0x3D: 'ROR (a,a)',  0x3E: 'ROR (a,b)',
    0x3F: 'ROL R',      0x40: 'ROL a',      0x41: 'ROL (a,a)',  0x42: 'ROL (a,b)',
    0x43: 'PHR R',      0x44: 'PHR #',      0x45: 'PHR a',      0x46: 'PHR (a,a)',  0x47: 'PHR (a,b)',
    0x48: 'PLR R',      0x49: 'PLR a',      0x4A: 'PLR (a,a)',  0x4B: 'PLR (a,b)',
    0x4C: 'PHP',        0x4D: 'PLP',
    0x4E: 'PEK r(off)', 0x4F: 'PEK r(reg)',
    0x50: 'BNE r',      0x51: 'BNE a',      0x52: 'BEQ r',      0x53: 'BEQ a',
    0x54: 'BCC r',      0x55: 'BCC a',      0x56: 'BCS r',      0x57: 'BCS a',
    0x58: 'BPL r',      0x59: 'BPL a',      0x5A: 'BMI r',      0x5B: 'BMI a',
    0x5C: 'BVC r',      0x5D: 'BVC a',      0x5E: 'BVS r',      0x5F: 'BVS a',
    0x60: 'JMP R',      0x61: 'JMP r',      0x62: 'JMP a',      0x63: 'JMP (a,a)',  0x64: 'JMP (a,b)',
    0x65: 'JSR a',      0x66: 'RTS',        0x67: 'RTI',
    0x68: 'LDR #',      0x69: 'LDR a',      0x6A: 'LDR (a,a)',  0x6B: 'LDR (a,b)',
    0x6C: 'STR a',      0x6D: 'STR (a,a)',  0x6E: 'STR (a,b)',
    0x6F: 'PSD R',      0x70: 'PSD #',      0x71: 'PSD a',      0x72: 'PSD (a,a)',  0x73: 'PSD (a,b)',
    0x74: 'PRD R',      0x75: 'PRD a',      0x76: 'PRD (a,a)',  0x77: 'PRD (a,b)',
    0x78: 'PIE',
    0x79: 'CLI',        0x7A: 'SEI',        0x7B: 'CLC',        0x7C: 'SEC',
    0x7D: 'CPC R',      0x7E: 'CSP R',      0x7F: 'CPR R',
}

FLAG_LABELS = ['CF=0','CF=1']  # bit 0
# Full flag combo name
def flag_name(f):
    return f"CF={'1' if f&1 else '0'} ZF={'1' if f&2 else '0'} NF={'1' if f&4 else '0'} VF={'1' if f&8 else '0'}"

# # ══════════════════════════════════════════════════════════════════════════════
# # OUTPUT FUNCTIONS
# # ══════════════════════════════════════════════════════════════════════════════

# def write_listing(filename):
#     BRANCH_OPCODES = set(range(0x50, 0x60))
#     with open(filename, 'w', encoding='utf-8') as f:
#         f.write("PCB-16 PopCornBoard — Microcode EEPROM Listing\n")
#         f.write("=" * 72 + "\n")
#         f.write("EEPROM: 32768 entries × 40 bits  |  Address = (OPCODE<<8)|(FLAGS<<4)|STEP\n")
#         f.write("FLAGS nibble: bit0=CF  bit1=ZF  bit2=NF  bit3=VF\n")
#         f.write("=" * 72 + "\n\n")
#         for opcode in range(128):
#             name = OPCODE_NAMES.get(opcode, 'RESERVED')
#             f.write(f"── 0x{opcode:02X}  {name}\n")
#             printed_any = False
#             last_steps = None
#             for flags in range(16):
#                 steps = []
#                 for step in range(16):
#                     cw = rom[rom_addr(opcode, flags, step)]
#                     steps.append(cw)
#                 while steps and steps[-1] == 0:
#                     steps.pop()
#                 if not steps:
#                     continue
#                 printed_any = True
#                 is_branch = opcode in BRANCH_OPCODES
#                 if not is_branch and steps == last_steps:
#                     continue
#                 last_steps = steps
#                 if is_branch:
#                     f.write(f"   [{flag_name(flags)}]\n")
#                 for i, cw in enumerate(steps):
#                     sigs = decode_cw(cw)
#                     f.write(f"   {i:2d}: 0x{cw:010X}  {', '.join(sigs) if sigs else '(none)'}\n")
#                 if is_branch:
#                     f.write("\n")
#             if not printed_any:
#                 f.write("   (unused / all zeros)\n")
#             f.write("\n")
#         f.write("── End of listing ──\n")

# def write_rom_dump(filename):
#     with open(filename, 'w') as f:
#         f.write("# PCB-16 EEPROM dump  |  addr(hex 15-bit) : data(hex 40-bit)\n")
#         f.write("# Only non-zero entries  |  addr = (opcode<<8)|(flags<<4)|step\n\n")
#         count = 0
#         for a in range(32768):
#             cw = rom[a]
#             if cw:
#                 f.write(f"{a:04X}: {cw:010X}\n")
#                 count += 1
#         f.write(f"\n# Non-zero entries: {count} / 32768\n")

# # def write_logisim(filename):
# #     with open(filename, 'w') as f:
# #         f.write("v3.0 hex words plain\n")
# #         entries = []
# #         i = 0
# #         while i < 32768:
# #             val = rom[i]
# #             run = 1
# #             while i + run < 32768 and rom[i + run] == val and run < 65535:
# #                 run += 1
# #             hx = f"{val:010x}"
# #             entries.append(f"{run}*{hx}" if run > 1 else hx)
# #             i += run
# #         for j in range(0, len(entries), 8):
# #             f.write(' '.join(entries[j:j+8]) + '\n')

# def write_logisim(filename):
#     with open(filename, 'w') as f:
#         # Vi deklarerar att det är plain text hex-värden
#         f.write("v3.0 hex words plain\n")
        
#         # Vi skriver ut exakt varje adress för sig, 8 ord per rad,
#         # och maskar med 0xFFFFFFFFFF för att säkerställa strikt 40-bitars bredd.
#         row_entries = []
#         for i in range(32768):
#             val = rom[i] & 0xFFFFFFFFFF  # Garantera 40 bitar
#             row_entries.append(f"{val:010x}")
            
#             # Var 8:e ord gör vi en ny rad för att hålla filen snygg och lättläst för Logisim
#             if len(row_entries) == 8:
#                 f.write(' '.join(row_entries) + '\n')
#                 row_entries = []
                
#         # Skriv ut eventuella resterande ord (om det mot förmodan inte gick jämnt ut)
#         if row_entries:
#             f.write(' '.join(row_entries) + '\n')

# # ══════════════════════════════════════════════════════════════════════════════
# # MAIN
# # ══════════════════════════════════════════════════════════════════════════════
# if __name__ == '__main__':
#     import os

#     print("PCB-16 PopCornBoard — Microcode EEPROM Generator")
#     non_zero = sum(1 for x in rom if x != 0)
#     print(f"Non-zero ROM entries: {non_zero} / 32768")

#     # ── Sanity checks ──────────────────────────────────────────────────────────
#     errors = 0
#     # 1. All defined opcodes reach INSTDONE
#     for opcode, name in OPCODE_NAMES.items():
#         for flags in range(16):
#             found = any(rom[rom_addr(opcode, flags, s)] & INSTDONE for s in range(16))
#             if not found:
#                 print(f"  ERROR: 0x{opcode:02X} {name} flags={flags:04b} never reaches INSTDONE")
#                 errors += 1
#     # 2. No step sequence exceeds 16 steps
#     for opcode in OPCODE_NAMES:
#         for flags in range(16):
#             for step in range(16):
#                 cw = rom[rom_addr(opcode, flags, step)]
#                 if cw & INSTDONE:
#                     break
#             else:
#                 print(f"  ERROR: 0x{opcode:02X} flags={flags:04b} still no INSTDONE at step 15")
#                 errors += 1

#     if errors == 0:
#         print("Sanity checks: all passed")
#     else:
#         print(f"Sanity checks: {errors} error(s)")

#     print("\nWriting files...")
#     write_listing('pcb16_microcode_listing.txt')
#     write_rom_dump('pcb16_rom_dump.txt')
#     write_logisim('pcb16_eeprom.img')

#     for fn in ['pcb16_microcode_listing.txt', 'pcb16_rom_dump.txt', 'pcb16_eeprom.img']:
#         print(f"  {fn}  ({os.path.getsize(fn):,} bytes)")


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def write_listing(filename):
    BRANCH_OPCODES = set(range(0x50, 0x60))
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("PCB-16 PopCornBoard — Microcode EEPROM Listing\n")
        f.write("=" * 72 + "\n")
        f.write("EEPROM: 32768 entries × 48 bits  |  Address = (OPCODE<<8)|(FLAGS<<4)|STEP\n")
        f.write("FLAGS nibble: bit0=CF  bit1=ZF  bit2=NF  bit3=VF\n")
        f.write("=" * 72 + "\n\n")
        for opcode in range(128):
            name = OPCODE_NAMES.get(opcode, 'RESERVED')
            f.write(f"── 0x{opcode:02X}  {name}\n")
            printed_any = False
            last_steps = None
            for flags in range(16):
                steps = []
                for step in range(16):
                    cw = rom[rom_addr(opcode, flags, step)]
                    steps.append(cw)
                while steps and steps[-1] == 0:
                    steps.pop()
                if not steps:
                    continue
                printed_any = True
                is_branch = opcode in BRANCH_OPCODES
                if not is_branch and steps == last_steps:
                    continue
                last_steps = steps
                if is_branch:
                    f.write(f"   [{flag_name(flags)}]\n")
                for i, cw in enumerate(steps):
                    sigs = decode_cw(cw)
                    # Formaterad för 12 hex-tecken (48 bitar)
                    f.write(f"   {i:2d}: 0x{cw:012X}  {', '.join(sigs) if sigs else '(none)'}\n")
                if is_branch:
                    f.write("\n")
            if not printed_any:
                f.write("   (unused / all zeros)\n")
            f.write("\n")
        f.write("── End of listing ──\n")

def write_rom_dump(filename):
    with open(filename, 'w') as f:
        f.write("# PCB-16 EEPROM dump  |  addr(hex 15-bit) : data(hex 48-bit)\n")
        f.write("# Only non-zero entries  |  addr = (opcode<<8)|(flags<<4)|step\n\n")
        count = 0
        for a in range(32768):
            cw = rom[a]
            if cw:
                # Formaterad för 12 hex-tecken (48 bitar)
                f.write(f"{a:04X}: {cw:012X}\n")
                count += 1
        f.write(f"\n# Non-zero entries: {count} / 32768\n")

def write_logisim(filename):
    with open(filename, 'w') as f:
        f.write("v3.0 hex words plain\n")
        
        row_entries = []
        for i in range(32768):
            val = rom[i] & 0xFFFFFFFFFFFF  # Maskad med 48 bitar (tolv F)
            row_entries.append(f"{val:012x}") # Formaterad för 12 hex-tecken (48 bitar)
            
            if len(row_entries) == 8:
                f.write(' '.join(row_entries) + '\n')
                row_entries = []
                
        if row_entries:
            f.write(' '.join(row_entries) + '\n')

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import os

    print("PCB-16 PopCornBoard — Microcode EEPROM Generator (48-bit Control Word)")
    non_zero = sum(1 for x in rom if x != 0)
    print(f"Non-zero ROM entries: {non_zero} / 32768")

    # ── Sanity checks ──────────────────────────────────────────────────────────
    errors = 0
    for opcode, name in OPCODE_NAMES.items():
        for flags in range(16):
            found = any(rom[rom_addr(opcode, flags, s)] & INSTDONE for s in range(16))
            if not found:
                print(f"  ERROR: 0x{opcode:02X} {name} flags={flags:04b} never reaches INSTDONE")
                errors += 1
    for opcode in OPCODE_NAMES:
        for flags in range(16):
            for step in range(16):
                cw = rom[rom_addr(opcode, flags, step)]
                if cw & INSTDONE:
                    break
            else:
                print(f"  ERROR: 0x{opcode:02X} flags={flags:04b} still no INSTDONE at step 15")
                errors += 1

    if errors == 0:
        print("Sanity checks: all passed")
    else:
        print(f"Sanity checks: {errors} error(s)")

    print("\nWriting files...")
    write_listing('microcode\\pcb16_microcode_listing.txt')
    write_rom_dump('microcode\\pcb16_rom_dump.txt')
    write_logisim('microcode\\pcb16_eeprom.img')

    for fn in ['microcode\\pcb16_microcode_listing.txt', 'microcode\\pcb16_rom_dump.txt', 'microcode\\pcb16_eeprom.img']:
        print(f"  {fn}  ({os.path.getsize(fn):,} bytes)")