import zipfile
import json
import os

# Filnamn
original_vsix = 'pcb16-asm-0.1.0.vsix'
output_vsix = 'pcb16-asm-vibrant-0.1.2.vsix'

if not os.path.exists(original_vsix):
    print(f"Fel: Hittade inte {original_vsix} i denna mapp!")
    exit()

print("Uppdaterar din VS Code extension till v0.1.2 med Julia Monokai Vibrant stöd...")

# 1. Defonition av den nya syntax-validatorn (JavaScript)
validator_js = """\"use strict\";
Object.defineProperty(exports, "__esModule", { value: true });
exports.validateLine = validateLine;
const vscode = require("vscode");
const instructions_1 = require("./instructions");

const REGEX_REG = `([A-H])`;
const REGEX_IMMEDIATE = `(#[\\\\$%""\\\\d]-?[\\\\dA-Fa-f]*)`;
const REGEX_ADDR = `([\\\\$%""\\\\d]-?[\\\\dA-Fa-f]*)`;

const syntaxMatchers = [
  { type: 'reg,reg,reg', regex: new RegExp(`^${REGEX_REG}\\\\s*,\\\\s*${REGEX_REG}\\\\s*,\\\\s*${REGEX_REG}$`, 'i') },
  { type: 'reg,#data', regex: new RegExp(`^${REGEX_REG}\\\\s*,\\\\s*${REGEX_IMMEDIATE}$`, 'i') },
  { type: 'reg,$addr', regex: new RegExp(`^${REGEX_REG}\\\\s*,\\\\s*${REGEX_ADDR}$`, 'i') },
  { type: 'reg,$addr,idx', regex: new RegExp(`^${REGEX_REG}\\\\s*,\\\\s*${REGEX_ADDR}\\\\s*,\\\\s*([AB])$`, 'i') },
  { type: 'reg,reg', regex: new RegExp(`^${REGEX_REG}\\\\s*,\\\\s*${REGEX_REG}$`, 'i') },
  { type: 'reg', regex: new RegExp(`^${REGEX_REG}$`, 'i') },
  { type: '#data', regex: new RegExp(`^${REGEX_IMMEDIATE}$`, 'i') },
  { type: 'addr', regex: new RegExp(`^${REGEX_ADDR}$`, 'i') },
  { type: 'none', regex: new RegExp(`^$`) }
];

function validateLine(document, lineIndex) {
  const lineText = document.lineAt(lineIndex).text;
  const diagnostics = [];
  const commentStart = lineText.indexOf(';');
  const codeText = commentStart !== -1 ? lineText.slice(0, commentStart) : lineText;
  const trimmedCode = codeText.trim();

  if (trimmedCode === '' || trimmedCode.startsWith('.') || 
      /^\\\\s*[A-Za-z_][A-Za-z0-9_]*\\\\s*=\\\\s*/.test(lineText) ||
      /^\\\\s*[A-Za-z_][A-Za-z0-9_]*\\\\s*:/.test(lineText)) {
    return diagnostics;
  }

  const parts = trimmedCode.split(/\\\\s+/);
  if (parts.length === 0) return diagnostics;
  const mnemonicRaw = parts[0];
  const operandsRaw = parts.slice(1).join(' ').trim();
  const mnemonicUpper = mnemonicRaw.toUpperCase();
  const instrInfo = instructions_1.INSTRUCTION_MAP.get(mnemonicUpper);
  const mnemonicRange = new vscode.Range(lineIndex, lineText.indexOf(mnemonicRaw), lineIndex, lineText.indexOf(mnemonicRaw) + mnemonicRaw.length);

  if (!instrInfo) {
    const diagnostic = new vscode.Diagnostic(mnemonicRange, `[PCB-16] Okänd instruktion: ${mnemonicRaw}`, vscode.DiagnosticSeverity.Error);
    diagnostic.code = 'unknown-mnemonic';
    diagnostics.push(diagnostic);
    return diagnostics;
  }

  const allowedSyntaxTypes = instrInfo.syntax.map(s => {
    const syntaxParts = s.split(/\\\\s+/);
    if (syntaxParts.length <= 1) return 'none';
    const operandDesc = syntaxParts[1].split(';')[0].trim();
    if (operandDesc.includes('regX,regY,regZ')) return 'reg,reg,reg';
    if (operandDesc.includes('regX,#data')) return 'reg,#data';
    if (operandDesc.includes('regX,$addr,A/B')) return 'reg,$addr,idx';
    if (operandDesc.includes('regX,$addr')) return 'reg,$addr';
    if (operandDesc.includes('regX,regY')) return 'reg,reg';
    if (operandDesc.includes('regX')) return 'reg';
    if (operandDesc.includes('#data')) return 'reg,#data';
    if (operandDesc.includes('$addr')) return 'addr';
    if (mnemonicUpper === 'BRK' || mnemonicUpper === 'PIE') return 'addr';
    if (mnemonicUpper === 'CPC') return 'reg';
    if (mnemonicUpper === 'INC' || mnemonicUpper === 'DEC') {
      if (operandDesc.includes('regX')) return 'reg';
      if (operandDesc.includes('$addr,A/B')) return 'addr,idx';
      if (operandDesc.includes('$addr')) return 'addr';
    }
    return 'none';
  });

  let isValid = false;
  let matchingSyntaxType = '';
  for (const allowedType of allowedSyntaxTypes) {
    const matcher = syntaxMatchers.find(m => m.type === allowedType);
    if (matcher && matcher.regex.test(operandsRaw)) {
      isValid = true;
      matchingSyntaxType = allowedType;
      break;
    }
  }

  if (!isValid && operandsRaw.length > 0) {
    const operandRange = new vscode.Range(lineIndex, lineText.indexOf(mnemonicRaw) + mnemonicRaw.length + 1, lineIndex, lineText.length);
    const diagnostic = new vscode.Diagnostic(operandRange, `[PCB-16] Ogiltig operand-syntax för ${mnemonicRaw}.`, vscode.DiagnosticSeverity.Error);
    diagnostic.relatedInformation = instrInfo.syntax.map(s => new vscode.DiagnosticRelatedInformation(mnemonicRange, `Giltig syntax: ${s.split(';')[0].trim()}`));
    diagnostic.code = 'invalid-operands';
    diagnostics.push(diagnostic);
  } else if (!isValid && operandsRaw.length === 0 && mnemonicUpper !== 'RST' && !allowedSyntaxTypes.includes('none')) {
    const diagnostic = new vscode.Diagnostic(mnemonicRange, `[PCB-16] Operander saknas för ${mnemonicRaw}.`, vscode.DiagnosticSeverity.Error);
    diagnostic.relatedInformation = instrInfo.syntax.map(s => new vscode.DiagnosticRelatedInformation(mnemonicRange, `Giltig syntax: ${s.split(';')[0].trim()}`));
    diagnostic.code = 'missing-operands';
    diagnostics.push(diagnostic);
  }

  if (matchingSyntaxType === 'reg,$addr,idx') {
    const partsIdx = operandsRaw.split(',');
    if (partsIdx.length === 3) {
      const idxReg = partsIdx[2].trim().toUpperCase();
      if (idxReg !== 'A' && idxReg !== 'B') {
        diagnostics.push(new vscode.Diagnostic(mnemonicRange, `[PCB-16] ${mnemonicRaw} stödjer bara indexering med A eller B.`, vscode.DiagnosticSeverity.Error));
      }
    }
  }
  return diagnostics;
}
"""

# 2. Definition av huvudtillägget med talkonvertering (JavaScript)
extension_js = """\"use strict\";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const instructions_1 = require("./instructions");
const instructionsValidator_1 = require("./instructionsValidator");

const LANG_ID = 'pcb16asm';
let diagnosticCollection;

function activate(context) {
    diagnosticCollection = vscode.languages.createDiagnosticCollection(LANG_ID);
    context.subscriptions.push(diagnosticCollection);
    context.subscriptions.push(
        vscode.languages.registerHoverProvider(LANG_ID, new Pcb16HoverProvider()),
        vscode.languages.registerCompletionItemProvider(LANG_ID, new Pcb16CompletionProvider(), ' ', ','),
        vscode.languages.registerOnTypeFormattingEditProvider(LANG_ID, new Pcb16OnTypeFormatter(), '\\\\n')
    );
    if (vscode.window.activeTextEditor) { updateDiagnostics(vscode.window.activeTextEditor.document); }
    context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(updateDiagnostics));
    context.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e => updateDiagnostics(e.document)));
    context.subscriptions.push(vscode.workspace.onDidCloseTextDocument(doc => diagnosticCollection.delete(doc.uri)));
}

function updateDiagnostics(document) {
    if (document.languageId !== LANG_ID) return;
    const diagnostics = [];
    for (let i = 0; i < document.lineCount; i++) {
        const lineDiagnostics = (0, instructionsValidator_1.validateLine)(document, i);
        if (lineDiagnostics.length > 0) { diagnostics.push(...lineDiagnostics); }
    }
    diagnosticCollection.set(document.uri, diagnostics);
}

function deactivate() {}

class Pcb16HoverProvider {
    provideHover(document, position) {
        const wordRange = document.getWordRangeAtPosition(position, /[A-Za-z0-9_%$#"-]+/);
        if (!wordRange) return undefined;
        const word = document.getText(wordRange);
        const upper = word.toUpperCase();
        let numberValue;

        if (/^\\\\$[0-9A-Fa-f]+$/.test(word)) { numberValue = parseInt(word.slice(1), 16); }
        elif (/^%[01]+$/.test(word)) { numberValue = parseInt(word.slice(1), 2); }
        elif (/^-?\\\\d+$/.test(word)) { numberValue = parseInt(word, 10); }
        elif (/^#[\\\\$%""\\\\d]/.test(word)) {
            const dataStr = word.slice(1);
            if (dataStr.startsWith('$')) numberValue = parseInt(dataStr.slice(1), 16);
            elif (dataStr.startsWith('%')) numberValue = parseInt(dataStr.slice(1), 2);
            elif (/^\\\\d+$/.test(dataStr)) numberValue = parseInt(dataStr, 10);
        }

        if (numberValue !== undefined && !isNaN(numberValue)) {
            const md = new vscode.MarkdownString();
            const signedDecimal = new Int16Array([numberValue])[0];
            const unsignedDecimal = new Uint16Array([numberValue])[0];
            md.appendMarkdown(`**PCB-16 Talkonvertering** (${word})\\\\n\\\\n`);
            md.appendCodeblock(`Hex:    \\\\$${unsignedDecimal.toString(16).toUpperCase()}\\\\n` +
                `Dec:    ${signedDecimal} (Signed 16-bit)\\\\n` +
                `        ${unsignedDecimal} (Unsigned 16-bit)\\\\n` +
                `Binär:  %${(unsignedDecimal >>> 0).toString(2).padStart(16, '0')}`, 'pcb16asm');
            const originalHover = this.provideOriginalHover(document, position, wordRange, upper, word);
            if (originalHover) {
                md.appendMarkdown('\\\\n---\\\\n');
                md.appendMarkdown(originalHover.contents[0].value);
            }
            return new vscode.Hover(md, wordRange);
        }
        return this.provideOriginalHover(document, position, wordRange, upper, word);
    }

    provideOriginalHover(document, position, wordRange, upper, word) {
        const instr = instructions_1.INSTRUCTION_MAP.get(upper);
        if (instr) {
            const md = new vscode.MarkdownString();
            md.appendMarkdown(`**${instr.mnemonic}** — ${instr.summary}\\\\\\n\\\\\\n`);
            md.appendCodeblock(instr.syntax.join('\\\\n'), 'pcb16asm');
            md.appendMarkdown(`\\\\n${instr.description}`);
            if (instr.flags) md.appendMarkdown(`\\\\n\\\\n**Flaggor:** ${instr.flags}`);
            return new vscode.Hover(md, wordRange);
        }
        const lineText = document.lineAt(position.line).text;
        const dotCheckRange = new vscode.Range(new vscode.Position(position.line, Math.max(0, wordRange.start.character - 1)), wordRange.end);
        if (document.getText(dotCheckRange).startsWith('.')) {
            const dirDoc = instructions_1.DIRECTIVES[document.getText(dotCheckRange).toLowerCase()];
            if (dirDoc) return new vscode.Hover(new vscode.MarkdownString(dirDoc), dotCheckRange);
        }
        if (instructions_1.REGISTERS.includes(upper) && word.length === 1) {
            return new vscode.Hover(new vscode.MarkdownString(instructions_1.REGISTER_INFO[upper] ?? `Register ${upper}`), wordRange);
        }
        return undefined;
    }
}

class Pcb16CompletionProvider {
    provideCompletionItems(document, position) {
        const linePrefix = document.lineAt(position).text.slice(0, position.character);
        const items = [];
        if (/^\\\\s*[A-Za-z]*$/.test(linePrefix)) {
            for (const instr of instructions_1.INSTRUCTIONS) {
                const item = new vscode.CompletionItem(instr.mnemonic, vscode.CompletionItemKind.Keyword);
                item.detail = instr.summary;
                item.documentation = new vscode.MarkdownString(instr.syntax.join('\\\\n') + '\\\\n\\\\n' + instr.description);
                items.push(item);
            }
            for (const [dir, doc] of Object.entries(instructions_1.DIRECTIVES)) {
                items.push(new vscode.CompletionItem(dir, vscode.CompletionItemKind.Keyword));
            }
            return items;
        }
        for (const reg of instructions_1.REGISTERS) {
            items.push(new vscode.CompletionItem(reg, vscode.CompletionItemKind.Variable));
        }
        return items;
    }
}

class Pcb16OnTypeFormatter {
    provideOnTypeFormattingEdits(document, position, ch) {
        if (position.line === 0) return [];
        const prevText = document.lineAt(position.line - 1).text;
        let newIndent = '';
        if (/^\\\\s*[A-Za-z_][A-Za-z0-9_]*\\\\s*:\\\\s*$/.test(prevText)) newIndent = '    ';
        elif (!/^\\\\s*\\\\.org\\\\b/i.test(prevText) && !/^\\\\s*(;.*)?$/.test(prevText)) {
            const m = /^(\\\\s*)/.exec(prevText);
            newIndent = m ? m[1] : '';
        }
        return [vscode.TextEdit.replace(new vscode.Range(new vscode.Position(position.line, 0), position), newIndent)];
    }
}
"""

# 3. Uppdaterad tmLanguage för Julia Monokai Vibrant färgteman
vibrant_grammar = {
  "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
  "name": "PCB-16 Assembly",
  "scopeName": "source.pcb16asm",
  "patterns": [
    { "include": "#comment" }, { "include": "#directive" }, { "include": "#constant-def" },
    { "include": "#label-def" }, { "include": "#instruction-line" }
  ],
  "repository": {
    "comment": { "patterns": [{ "name": "comment.line.semicolon.pcb16asm", "match": ";.*$" }] },
    "directive": { "patterns": [{ "name": "storage.type.directive.pcb16asm", "match": "(?i)^\\\\s*(\\\\.org|\\\\.word)\\\\b" }] },
    "constant-def": { "patterns": [{ "match": "^\\\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*(=)\\\\s*", "captures": { "1": { "name": "variable.other.constant.pcb16asm" }, "2": { "name": "keyword.operator.assignment.pcb16asm" } } }] },
    "label-def": { "patterns": [{ "match": "^\\\\s*([A-Za-z_][A-Za-z0-9_]*)\\\\s*(:)", "captures": { "1": { "name": "entity.name.function.label.pcb16asm" }, "2": { "name": "punctuation.separator.label.pcb16asm" } } }] },
    "instruction-line": { "patterns": [
        { "include": "#mnemonic" }, { "include": "#register" }, { "include": "#number-immediate" },
        { "include": "#number-hex" }, { "include": "#number-bin" }, { "include": "#number-ascii" },
        { "include": "#number-decimal" }, { "include": "#index-suffix" }, { "include": "#punctuation" }
    ]},
    "mnemonic": { "patterns": [{ "name": "keyword.control.instruction.pcb16asm", "match": "(?i)\\\\b(RST|HLT|BRK|WAI|ADC|SBC|NEG|OR|AND|XOR|CMP|BIT|INC|DEC|LSR|LSL|ROR|ROL|PHR|PLR|PHP|PLP|BNE|BEQ|BCC|BCS|BPL|BMI|BVC|BVS|JMP|JSR|RTS|RTI|LDR|STR|PIE|CLI|SEI|CLC|SEC|CPC|CSP|CPR)\\\\b" }] },
    "register": { "patterns": [{ "name": "variable.parameter.register.pcb16asm", "match": "(?i)\\\\b[A-H]\\\\b" }] },
    "number-immediate": { "patterns": [{ "match": "(#)(\\\\$[0-9A-Fa-f]+|%+|\\\"\\\\\\\\?.\\\"|-?\\\\d+)", "captures": { "1": { "name": "keyword.operator.immediate.pcb16asm" }, "2": { "name": "constant.numeric.pcb16asm" } } }] },
    "number-hex": { "patterns": [{ "name": "constant.numeric.hex.pcb16asm", "match": "\\\\$[0-9A-Fa-f]+\\\\b" }] },
    "number-bin": { "patterns": [{ "name": "constant.numeric.binary.pcb16asm", "match": "%+\\\\b" }] },
    "number-ascii": { "patterns": [{ "name": "constant.character.pcb16asm", "match": "\\\"\\\\\\\\?.\\\"" }] },
    "number-decimal": { "patterns": [{ "name": "constant.numeric.decimal.pcb16asm", "match": "(?<![A-Za-z0-9_$])-?\\\\d+\\\\b" }] },
    "index-suffix": { "patterns": [{ "match": "(,)\\\\s*(?i:[AB])\\\\b", "captures": { "1": { "name": "punctuation.separator.comma.pcb16asm" }, "2": { "name": "variable.parameter.index-register.pcb16asm" } } }] },
    "punctuation": { "patterns": [{ "name": "punctuation.separator.comma.pcb16asm", "match": "," }] }
  }
}

# Processa och packa om VSIX
with zipfile.ZipFile(output_vsix, 'w', zipfile.ZIP_DEFLATED) as z_out:
    with zipfile.ZipFile(original_vsix, 'r') as z_in:
        for item in z_in.infolist():
            if item.filename == 'extension/package.json':
                pkg = json.loads(z_in.read(item.filename))
                pkg['version'] = '0.1.2'
                z_out.writestr(item, json.dumps(pkg, indent=2))
            elif item.filename == 'extension/syntaxes/pcb16asm.tmLanguage.json':
                z_out.writestr(item, json.dumps(vibrant_grammar, indent=2))
            elif item.filename == 'extension/out/extension.js':
                z_out.writestr(item, extension_js)
            else:
                z_out.writestr(item, z_in.read(item.filename))
        
        # Sätt in den helt nya validatorn
        z_out.writestr('extension/out/instructionsValidator.js', validator_js)

print(f"KLART! Filen '{output_vsix}' har skapats framgångsrikt i denna mapp.")
