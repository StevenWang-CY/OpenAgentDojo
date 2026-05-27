/**
 * P1-3 — LSP ↔ Monaco translators.
 *
 * Kept narrow on purpose: only the three editor surfaces we promised
 * (hover, completion, diagnostics) need translation, and each
 * translator is a pure function over the LSP wire shape. The Monaco
 * types are intentionally referenced via the workspace-local
 * ``monaco-editor`` import (the same one ``@monaco-editor/react`` uses
 * under the hood) so the editor instance and these helpers agree on
 * enum values.
 */
import type { editor, languages, IRange } from "monaco-editor";
import type {
  CompletionItem,
  CompletionList,
  HoverContent,
  LSPDiagnostic,
} from "./client";

/**
 * Translate an LSP ``Diagnostic[]`` into Monaco ``IMarkerData[]`` for use
 * with ``editor.setModelMarkers``. The ``owner`` argument is the marker
 * source — usually ``"lsp:<language>"`` — and is also stamped on each
 * marker's ``source`` so multiple LSPs on the same model don't fight.
 */
export function diagnosticsToMarkers(
  diagnostics: LSPDiagnostic[],
  source: string
): editor.IMarkerData[] {
  return diagnostics.map((d) => ({
    severity: lspSeverityToMonaco(d.severity),
    message: d.message,
    startLineNumber: d.range.start.line + 1,
    startColumn: d.range.start.character + 1,
    endLineNumber: d.range.end.line + 1,
    endColumn: d.range.end.character + 1,
    code: d.code !== undefined ? String(d.code) : undefined,
    source: d.source ?? source,
  }));
}

function lspSeverityToMonaco(
  sev: LSPDiagnostic["severity"]
): editor.IMarkerData["severity"] {
  // LSP: 1 = Error, 2 = Warning, 3 = Information, 4 = Hint
  // Monaco MarkerSeverity: 8 = Error, 4 = Warning, 2 = Info, 1 = Hint.
  // Hard-coded to keep this helper free of a heavy monaco import side-effect.
  switch (sev) {
    case 1:
      return 8;
    case 2:
      return 4;
    case 3:
      return 2;
    case 4:
      return 1;
    default:
      return 8;
  }
}

/**
 * Translate an LSP ``CompletionList`` (or bare ``CompletionItem[]``) into
 * the shape Monaco's CompletionItemProvider expects.
 */
export function completionsToMonaco(
  list: CompletionList | CompletionItem[] | null,
  range: IRange
): languages.CompletionList | null {
  if (!list) return null;
  const items = Array.isArray(list) ? list : list.items;
  if (!Array.isArray(items)) return null;
  const out: languages.CompletionItem[] = items.map((item) => ({
    label: item.label,
    insertText: item.insertText ?? item.label,
    kind: lspCompletionKindToMonaco(item.kind),
    detail: item.detail,
    documentation:
      typeof item.documentation === "string"
        ? item.documentation
        : item.documentation?.value,
    filterText: item.filterText,
    sortText: item.sortText,
    range,
  }));
  return {
    suggestions: out,
    incomplete: Array.isArray(list) ? false : Boolean(list.isIncomplete),
  };
}

/**
 * LSP CompletionItemKind (spec values 1-25, see
 * https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#completionItemKind)
 * → Monaco ``languages.CompletionItemKind`` enum members.
 *
 * Built as a deterministic table so the FE never relies on the raw
 * numeric values (which differ between LSP and Monaco — Monaco's
 * ``Method`` is 0 while LSP's is 2, and the two specs have drifted
 * over time as new kinds were added). FE-P4 audit fix.
 */
function lspCompletionKindToMonaco(
  kind: number | undefined
): languages.CompletionItemKind {
  // The Monaco namespace itself is provided to us at runtime via
  // ``@monaco-editor/react``; this module never imports the monaco
  // runtime so we hard-code the enum-equivalent values from the
  // monaco-editor type declaration to keep the helper tree-shake-
  // friendly. The values below are the public ``CompletionItemKind``
  // enum members copied from the ``monaco-editor`` typings.
  //
  //   Method = 0, Function = 1, Constructor = 2, Field = 3,
  //   Variable = 4, Class = 5, Struct = 6, Interface = 7,
  //   Module = 8, Property = 9, Event = 10, Operator = 11,
  //   Unit = 12, Value = 13, Constant = 14, Enum = 15,
  //   EnumMember = 16, Keyword = 17, Text = 18, Color = 19,
  //   File = 20, Reference = 21, Customcolor = 22, Folder = 23,
  //   TypeParameter = 24, User = 25, Issue = 26, Snippet = 27
  type MonacoKind = languages.CompletionItemKind;
  const Method = 0 as MonacoKind;
  const Function = 1 as MonacoKind;
  const Constructor = 2 as MonacoKind;
  const Field = 3 as MonacoKind;
  const Variable = 4 as MonacoKind;
  const Class = 5 as MonacoKind;
  const Struct = 6 as MonacoKind;
  const Interface = 7 as MonacoKind;
  const Module = 8 as MonacoKind;
  const Property = 9 as MonacoKind;
  const Event = 10 as MonacoKind;
  const Operator = 11 as MonacoKind;
  const Unit = 12 as MonacoKind;
  const Value = 13 as MonacoKind;
  const Constant = 14 as MonacoKind;
  const Enum = 15 as MonacoKind;
  const EnumMember = 16 as MonacoKind;
  const Keyword = 17 as MonacoKind;
  const Text = 18 as MonacoKind;
  const Color = 19 as MonacoKind;
  const File = 20 as MonacoKind;
  const Reference = 21 as MonacoKind;
  const Folder = 23 as MonacoKind;
  const TypeParameter = 24 as MonacoKind;
  const Snippet = 27 as MonacoKind;
  // LSP CompletionItemKind values (1-25). Anything we don't recognise
  // falls back to ``Text`` so the suggestion still renders with a
  // neutral icon.
  switch (kind) {
    case 1: // LSP Text
      return Text;
    case 2: // LSP Method
      return Method;
    case 3: // LSP Function
      return Function;
    case 4: // LSP Constructor
      return Constructor;
    case 5: // LSP Field
      return Field;
    case 6: // LSP Variable
      return Variable;
    case 7: // LSP Class
      return Class;
    case 8: // LSP Interface
      return Interface;
    case 9: // LSP Module
      return Module;
    case 10: // LSP Property
      return Property;
    case 11: // LSP Unit
      return Unit;
    case 12: // LSP Value
      return Value;
    case 13: // LSP Enum
      return Enum;
    case 14: // LSP Keyword
      return Keyword;
    case 15: // LSP Snippet
      return Snippet;
    case 16: // LSP Color
      return Color;
    case 17: // LSP File
      return File;
    case 18: // LSP Reference
      return Reference;
    case 19: // LSP Folder
      return Folder;
    case 20: // LSP EnumMember
      return EnumMember;
    case 21: // LSP Constant
      return Constant;
    case 22: // LSP Struct
      return Struct;
    case 23: // LSP Event
      return Event;
    case 24: // LSP Operator
      return Operator;
    case 25: // LSP TypeParameter
      return TypeParameter;
    default:
      return Text;
  }
}

/**
 * Translate an LSP hover response into Monaco's ``languages.Hover`` shape.
 * Returns null for an empty / missing hover so Monaco doesn't render an
 * empty popover.
 */
export function hoverToMonaco(
  hover: HoverContent | null
): languages.Hover | null {
  if (!hover || hover.contents === undefined || hover.contents === null) {
    return null;
  }
  const contents = normaliseHoverContents(hover.contents);
  if (contents.length === 0) return null;
  const monacoRange = hover.range
    ? {
        startLineNumber: hover.range.start.line + 1,
        startColumn: hover.range.start.character + 1,
        endLineNumber: hover.range.end.line + 1,
        endColumn: hover.range.end.character + 1,
      }
    : undefined;
  return {
    contents,
    range: monacoRange,
  };
}

function normaliseHoverContents(
  raw: unknown
): { value: string; isTrusted?: boolean }[] {
  // LSP allows: MarkupContent | MarkedString | MarkedString[]
  //   MarkupContent  = { kind: 'markdown'|'plaintext', value: string }
  //   MarkedString   = string | { language: string, value: string }
  if (typeof raw === "string") return [{ value: raw }];
  if (Array.isArray(raw)) {
    return raw
      .map((entry) => normaliseHoverContents(entry))
      .reduce<{ value: string }[]>((acc, x) => acc.concat(x), []);
  }
  if (raw && typeof raw === "object") {
    const obj = raw as { kind?: string; language?: string; value?: string };
    if (typeof obj.value === "string") {
      if (typeof obj.language === "string" && obj.language) {
        return [{ value: "```" + obj.language + "\n" + obj.value + "\n```" }];
      }
      return [{ value: obj.value }];
    }
  }
  return [];
}
